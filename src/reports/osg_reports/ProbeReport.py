import xml.etree.ElementTree as ET
import urllib2
import ast
import os
import re
import smtplib
import email.utils
from email.mime.text import MIMEText
import datetime
import logging
import json
import traceback
import sys

from elasticsearch_dsl import Search, Q

from . import Reporter, runerror, get_configfile
from . import Configuration

logfile = 'probereport.log'
now = datetime.datetime.now()
today = now.date()


@Reporter.init_reporter_parser
def parse_opts(parser):
    """
    Don't need to add any options to Reporter.parse_opts
    """
    pass


class OIMInfo(object):
    """Class to hold and operate on OIM information

    :param bool verbose: Verbose flag
    """
    def __init__(self, verbose=False):
        self.verbose = verbose
        self.logfile = logfile
        self.logger = self.setupgenLogger("ProbeReport-OIM")
        self.root = None
        self.resourcedict = {}

        self.dateslist = self.dateslist_init()
        self.xml_file = self.get_file_from_OIM()
        if self.xml_file:
            self.rgparse_xml()
            self.logger.info('Successfully parsed OIM file')
        else:
            raise

    def setupgenLogger(self, reportname):
        """Create logger for this class

        :param str reportname: Name of report that gets put in the log
        :return logger: Note that this logger is separte from the ProbeReport
        logger, though it writes to the same file
        """
        logger = logging.getLogger(reportname)
        logger.setLevel(logging.DEBUG)

        # Console handler - info
        ch = logging.StreamHandler()
        if self.verbose:
            ch.setLevel(logging.INFO)
        else:
            ch.setLevel(logging.WARNING)

        # FileHandler
        fh = logging.FileHandler(self.logfile)
        fh.setLevel(logging.DEBUG)
        logfileformat = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        fh.setFormatter(logfileformat)

        logger.addHandler(ch)
        logger.addHandler(fh)

        return logger

    def dateslist_init(self):
        """Creates dates lists to get passed into OIM urls"""
        startdate = today - datetime.timedelta(days=7)
        rawdateslist = [startdate.month, startdate.day, startdate.year,
                        today.month, today.day, today.year]
        return ['0' + str(elt) if len(str(elt)) == 1 else str(elt)
                     for elt in rawdateslist]

    def get_file_from_OIM(self, rg=True):
        """Get RG file from OIM for parsing, return the XML file

        :param bool rg: If true, go to the resource group OIM page and grab
        that data.  If false, get downtimes page

        :return Response: Response from URL we tried to contact
        """
        if rg:
            oim_url = 'http://myosg.grid.iu.edu/rgsummary/xml?' \
                  'summary_attrs_showhierarchy=on&summary_attrs_showwlcg=on' \
                  '&summary_attrs_showservice=on&summary_attrs_showfqdn=on' \
                  '&gip_status_attrs_showtestresults=on' \
                  '&downtime_attrs_showpast=&account_type=cumulative_hours' \
                  '&ce_account_type=gip_vo&se_account_type=vo_transfer_volume' \
                  '&bdiitree_type=total_jobs&bdii_object=service' \
                  '&bdii_server=is-osg&start_type=7daysago' \
                  '&start_date={0}%2F{1}%2F{2}&end_type=now' \
                  '&end_date={3}%2F{4}%2F{5}&all_resources=on' \
                  '&facility_sel%5B%5D=10009&gridtype=on&gridtype_1=on' \
                  '&service=on&service_sel%5B%5D=1&active=on&active_value=1' \
                  '&disable=on&disable_value=0&has_wlcg=on'.format(*self.dateslist)
            label = "Resource Group"
        else:
            oim_url = 'http://myosg.grid.iu.edu/rgdowntime/xml?' \
                     'summary_attrs_showservice=on&' \
                     'summary_attrs_showrsvstatus=on&summary_attrs_showfqdn=on&' \
                     'gip_status_attrs_showtestresults=on&downtime_attrs_showpast=&' \
                     'account_type=cumulative_hours&ce_account_type=gip_vo&' \
                     'se_account_type=vo_transfer_volume&bdiitree_type=total_jobs&' \
                     'bdii_object=service&bdii_server=is-osg&start_type=7daysago&' \
                     'start_date={0}%2F{1}%2F{2}&end_type=now&end_date={3}%2F{4}%2F{5}&' \
                     'all_resources=on&facility_sel%5B%5D=10009&gridtype=on&' \
                     'gridtype_1=on&service=on&service_sel%5B%5D=1&active=on&' \
                     'active_value=1&disable=on&disable_value=0&has_wlcg=on'.format(*self.dateslist)
            label = "Downtimes"

        if self.verbose:
            self.logger.info(oim_url)

        try:
            oim_xml = urllib2.urlopen(oim_url)
            self.logger.info("Got OIM {0} file successfully".format(label))
        except (urllib2.HTTPError, urllib2.URLError) as e:
            self.logger.error("Couldn't get OIM {0} file".format(label))
            self.logger.exception(e)
            if rg:
                sys.exit(1)
            else:
                return None

        return oim_xml

    def parse(self, other_xml_file=False):
        """
        Parse XML file

        :param bool other_xml_file: If true, we're looking at a downtimes file.
        If false, we're looking at an RG file.
        :return: XML etree root
        """
        if other_xml_file:
            xml_file = other_xml_file
            exit_on_fail = False
            label = "Downtimes"
        else:
            xml_file = self.xml_file
            exit_on_fail = True
            label = "Resource Group"
        try:
            tree = ET.parse(xml_file)
            self.logger.info("Parsing OIM {0} File".format(label))
            root = tree.getroot()
        except Exception as e:
            self.logger.error("Couldn't parse OIM {0} File".format(label))
            self.logger.exception(e)
            if exit_on_fail:
                sys.exit(1)
            else:
                return None

        return root

    def rgparse_xml(self):
        """Take the RG XML file and get relevant information, store it in class
        structures"""
        self.root = self.parse()
        for resourcename_elt in self.root.findall('./ResourceGroup/Resources/Resource'
                                             '/Name'):
            resourcename = resourcename_elt.text

            # Check that resource is active
            activepath = './ResourceGroup/Resources/Resource/' \
                         '[Name="{0}"]/Active'.format(resourcename)
            if not ast.literal_eval(self.root.find(activepath).text):
                continue

            # Skip if resource is disabled
            disablepath = './ResourceGroup/Resources/Resource/' \
                         '[Name="{0}"]/Disable'.format(resourcename)
            if ast.literal_eval(self.root.find(disablepath).text):
                continue

            if resourcename not in self.resourcedict:
                resource_grouppath = './ResourceGroup/Resources/Resource/' \
                                     '[Name="{0}"]/../..'.format(resourcename)
                self.resourcedict[resourcename] = \
                    self.get_resource_information(resource_grouppath,
                                                  resourcename)
        return

    def get_resource_information(self, rgpath, rname):
        """
        Get Resource Info from OIM

        :param str rgpath: XPath path to Resource Group Element to be parsed
        :param rname: Name of resource
        :return dict: dictionary that has relevant OIM information
        """
        # This could (and probably should) be moved to a config file
        rg_pathdictionary = {
            'Facility': './Facility/Name',
            'Site': './Site/Name',
            'ResourceGroup': './GroupName'}

        r_pathdictionary = {
            'Resource': './Name',
            'ID': './ID',
            'FQDN': './FQDN',
            'WLCGInteropAcct': './WLCGInformation/InteropAccounting'
        }

        returndict = {}

        # Resource group-specific info
        resource_group_elt = self.root.find(rgpath)
        for key, path in rg_pathdictionary.iteritems():
            try:
                returndict[key] = resource_group_elt.find(path).text
            except AttributeError:
                # Skip this.  It means there's no information for this key
                pass

        # Resource-specific info
        resource_elt = resource_group_elt.find(
            './Resources/Resource/[Name="{0}"]'.format(rname))
        for key, path in r_pathdictionary.iteritems():
            try:
                returndict[key] = resource_elt.find(path).text
            except AttributeError:
                # Skip this.  It means there's no information for this key
                pass

        return returndict

    def get_downtimes(self):
        """Get downtimes from OIM, return list of probes on resources that are
        in downtime currently

        :return list: List of probe FQDNs that are currently in downtime
        """
        nolist = []
        xml_file = self.get_file_from_OIM(rg=False)
        if not xml_file:
            return nolist

        root = self.parse(xml_file)
        if not root:
            return nolist

        down_fqdns = []

        for dtelt in root.findall('./CurrentDowntimes/Downtime'):
            fqdn = dtelt.find('./ResourceFQDN').text
            dstime = datetime.datetime.strptime(dtelt.find('./StartTime').text,
                                                "%b %d, %Y %H:%M %p UTC")
            detime = datetime.datetime.strptime(dtelt.find('./EndTime').text,
                                                "%b %d, %Y %H:%M %p UTC")
            if dstime < now < detime:
                self.logger.info("{0} in downtime".format(fqdn))
                down_fqdns.append(fqdn)

        return down_fqdns

    def get_fqdns_for_probes(self):
        """Parses resource dictionary and grabs the FQDNs and Resource Names
        if the resource is flagged as WLCG Interop Accting = True

        :return dict: dictionary with FQDNs and Resource Names
        """
        downtimes = self.get_downtimes()
        oim_probe_dict = {}
        for resourcename, info in self.resourcedict.iteritems():
            if ast.literal_eval(info['WLCGInteropAcct']) and \
                            info['FQDN'] not in downtimes:
                oim_probe_dict[info['FQDN']] = info['Resource']
        return oim_probe_dict


class ProbeReport(Reporter):
    """
    Class to hold information about and generate the probe report

    :param Configuration.Configuration config: Report Configuration object
    :param str start: Start time of report range
    :param bool verbose: Verbose flag
    :param bool is_test: Whether or not this is a test run.
    :param bool no_email: If true, don't actually send the email
    """
    def __init__(self, config, start, verbose=False, is_test=False,
                 no_email=False):
        report = "Probe"
        Reporter.__init__(self, report, config, start, end=start,
                          verbose=verbose, logfile=logfile, is_test=is_test,
                          no_email=no_email, allraw=True)
        self.configuration = config
        self.probematch = re.compile("(.+):(.+)")
        self.estimeformat = re.compile("(.+)T(.+)\.\d+Z")
        self.emailfile = 'filetoemail.txt'
        self.probe, self.resource = None, None
        self.historyfile = 'probereporthistory.log'
        self.newhistory = []
        self.reminder = False

    def query(self):
        """Method to query Elasticsearch cluster for Flocking Report
        information

        :return elasticsearch_dsl.Search: Search object containing ES query
        """
        startdateq = self.dateparse_to_iso(self.start_time)

        s = Search(using=self.client, index=self.indexpattern)\
            .filter(Q({"range": {"@received": {"gte": "{0}".format(startdateq)}}}))\
            .filter("term", ResourceType="Batch")

        s.aggs.bucket('group_probename', 'terms', field='ProbeName',
                               size=2**31-1)

        return s

    def lastreportinit(self):
        """Reset the start/end times for the ES query and generate a new
        index pattern based on those"""
        self.start_time = today.replace(
            day=1) - datetime.timedelta(days=1)
        self.end_time = today
        self.indexpattern = self.indexpattern_generate(allraw=True)

        if self.verbose:
            print "New index pattern is {0}".format(self.indexpattern)

        return

    def lastreportquery(self):
        """Queries ES to find the last time that a probe reported in.
        Returns a string with either that time or a string indicating that
        it has been over a month.

        :return str: String describing last report date of a probe
        """
        ls = Search(using=self.client, index=self.indexpattern)\
            .filter(Q({"range":{"@received":{"gte":"now-1M"}}}))\
            .filter("term", ResourceType="Batch")\
            .filter("wildcard", ProbeName="*:{0}".format(self.probe))

        ls.aggs.metric('datemax', 'max', field='@received')

        try:
            aggs = ls.execute().aggregations
        except Exception as e:
            self.logger.exception(e)
            runerror(self.configuration, e, traceback.format_exc())
            sys.exit(1)

        try:
            rawdate = aggs.datemax.value_as_string
            return "{0} at {1} UTC".format(
                *self.estimeformat.match(rawdate)
                .groups())
        except AttributeError:     # no value_as_string = no result
            return "over 1 month ago"
        except Exception as e:
            self.logger.exception(e)

    def get_probenames(self):
        """Function that parses the results of the elasticsearch query and
        parses the ProbeName field for the FQDN of the probename

        :return set: Set of all FQDNs of the probes returned by the ES query
        """
        proberecords = (rec for rec in self.results.group_probename.buckets)
        probenames = (self.probematch.match(proberecord.key)
                      for proberecord in proberecords)
        probes = (probename.group(2).lower()
                  for probename in probenames if probename)
        return set(probes)

    def generate(self, oimdict):
        """Higher-level method that calls the lower-level functions to
        generate the raw data for this report.

        :param dict oimdict:

        :return set: set of probes that are in OIM but not in the last two days of
        records.
        """
        resultset = self.query()

        t = resultset.to_dict()
        if self.verbose:
            print self.indexpattern
            print json.dumps(t, sort_keys=True, indent=4)
            self.logger.debug(json.dumps(t, sort_keys=True))
        else:
            self.logger.debug(json.dumps(t, sort_keys=True))

        try:
            response = resultset.execute()
            if not response.success():
                raise Exception("Error accessing Elasticsearch")

            self.results = response.aggregations
            self.logger.info("Successfully queried Elasticsearch")
        except Exception as e:
            self.logger.exception(e)
            runerror(self.configuration, e, traceback.format_exc())
            sys.exit(1)

        probes = self.get_probenames()

        if self.verbose:
            self.logger.info("Probes in last two days of records: {0}".format(sorted(probes)))

        self.logger.info("Successfully analyzed ES data vs. OIM data")
        oimset = set((key for key in oimdict))
        return oimset.difference(probes)

    def getprev_reported_probes(self):
        """Generator function that yields the probes from the previously
        reported file, as well as whether the previous report date was recent
        or not.  'Recent' is defined in the ::cutoff:: variable.
        """
        # Cutoff is a week ago, probrepdate is last report date for
        # a probe
        cutoff = today - datetime.timedelta(days=7)
        with open(self.historyfile, 'r') as h:
            for line in h:
                proberepdate = datetime.date(
                    *self.dateparse(re.split('\t', line)[1].strip())[:3])
                curprobe = re.split('\t', line)[0]

                if proberepdate > cutoff:
                    self.newhistory.append(line)  # Append line to new history
                    self.logger.debug("{0} has been reported on in the past"
                                      " week.  Will not resend report".format(
                        curprobe))
                    prev_reported_recent = True
                else:
                    prev_reported_recent = False

                yield curprobe, prev_reported_recent

    def generate_report_file(self, oimdict):
        """Generator function that generates the report files to send in email.
        This is where we exclude sending emails for those probes we've reported
        on in the last week.

        Yields if there are emails to send, returns otherwise"""
        missingprobes = self.generate(oimdict)

        prev_reported = set()
        prev_reported_recent = set()
        if os.path.exists(self.historyfile):
            for curprobe, is_recent_probe in self.getprev_reported_probes():
                prev_reported.add(curprobe)
                if is_recent_probe:
                    prev_reported_recent.add(curprobe)

        assert prev_reported.issuperset(prev_reported_recent)
        prev_reported_old = prev_reported.difference(prev_reported_recent)
        assert prev_reported.issuperset(prev_reported_old)

        self.lastreportinit()
        for elt in missingprobes.difference(prev_reported_recent):
            # Only operate on probes that weren't reported in the last week
            self.probe = elt
            self.resource = oimdict[elt]
            self.lastreport_date = self.lastreportquery()

            if self.probe in prev_reported_old:
                self.reminder = True    # Reminder flag
            else:
                self.reminder = False

            with open(self.emailfile, 'w') as f:
                # Generate email file
                f.write(self.emailtext())

            # Append line to new history
            self.newhistory.append('{0}\t{1}\n'.format(
                elt, today))
            yield

        return

    def emailsubject(self):
        """Format the subject for our emails"""
        if self.reminder:
            remindertext = 'REMINDER: '
        else:
            remindertext = ''
        return "{0}{1} Reporting Account Failure dated {2}"\
            .format(remindertext, self.resource, today)

    def emailtext(self):
        """Format the text for our emails"""
        text = 'The probe installed on {0} at {1} has not reported'\
               ' GRACC records to OSG for the last two days. The last ' \
               'date we received a record from {0} was {2}.  If this '\
               'is due to maintenance or a retirement of this '\
               'node, please let us know.  If not, please check to see '\
               'if your Gratia reporting is active.  The GRACC page at '\
               'https://gracc.opensciencegrid.org/dashboard/db/probe-status'\
               ' shows the latest date a probe has reported records to OSG.'.format(self.probe, self.resource, self.lastreport_date)
        return text

    def send_report(self):
        """Send our emails"""
        emailfrom = self.email_info["from_email"]
        emailsto = self.email_info["to_emails"]

        if self.test_no_email(emailsto):
            self.logger.info("Resource name: {0}\tProbe Name: {1}"
                             .format(self.resource, self.probe))

            if os.path.exists(self.emailfile):
                os.unlink(self.emailfile)

            return

        with open(self.emailfile, 'rb') as fp:
            msg = MIMEText(fp.read())

        msg['To'] = ', '.join(emailsto)
        msg['From'] = email.utils.formataddr((self.email_info["from_name"],
                                              emailfrom))
        msg['Subject'] = self.emailsubject()

        try:
            smtpObj = smtplib.SMTP(self.email_info["smtphost"])
            smtpObj.sendmail(emailfrom, emailsto, msg.as_string())
            smtpObj.quit()
            self.logger.info("Sent Email for {0}".format(self.resource))
            os.unlink(self.emailfile)
        except Exception as e:
            self.logger.exception("Error:  unable to send email.\n{0}\n".format(e))
            raise

        return

    def cleanup_history(self):
        """Clean up our history file.  We basically rewrite the entire history
        file based on what was populated into self.newhistory in the
        generate_report_file method of this class
        """
        with open(self.historyfile, 'w') as cleanup:
            for line in self.newhistory:
                cleanup.write(line)
        return

    def run_report(self, oimdict):
        """The higher level method that controls the generation and sending
        of the probe report using other methods in this class."""
        rep_files = self.generate_report_file(oimdict)

        try:
            for _ in rep_files:
                self.send_report()
        except Exception as e:
            self.logger.exception(e)

        self.logger.info('All new reports sent')
        self.cleanup_history()
        return


def main():
    args = parse_opts()

    # Set up the configuration
    config = Configuration.Configuration()
    config.configure(get_configfile(override=args.config))

    try:
        # Get OIM Information
        oiminfo = OIMInfo(args.verbose)
        oim_probe_fqdn_dict = oiminfo.get_fqdns_for_probes()

        startdate = today - datetime.timedelta(days=2)

        # Set up and send probe report
        preport = ProbeReport(config,
                              startdate,
                              verbose=args.verbose,
                              is_test=args.is_test,
                              no_email=args.no_email)

        preport.run_report(oim_probe_fqdn_dict)
        print 'Probe Report Execution finished'
    except Exception as e:
        with open(logfile, 'a') as f:
            f.write(traceback.format_exc())
        print >> sys.stderr, traceback.format_exc()
        runerror(config, e, traceback.format_exc())
        sys.exit(1)

    return

if __name__ == '__main__':
    main()
    sys.exit(0)