# encoding: utf-8

# Nagstamon - Nagios status monitor for your desktop
# Copyright (C) 2008-2014 Henri Wahl <h.wahl@ifw-dresden.de> et al.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA

import sys
import urllib.request, urllib.parse, urllib.error
import webbrowser
import copy

#from Nagstamon import Helpers
from Nagstamon.Objects import (GenericHost, GenericService, Result)
from Nagstamon.Servers.Generic import GenericServer
from Nagstamon.Helpers import HumanReadableDurationFromSeconds


class OpsviewService(GenericService):
    """
        add Opsview specific service property to generic service class
    """
    service_object_id = ""


class OpsviewServer(GenericServer):
    """
       special treatment for Opsview XML based API
    """
    TYPE = u'Opsview'

    # Arguments available for submitting check results
    SUBMIT_CHECK_RESULT_ARGS = ["comment"]

    # URLs for browser shortlinks/buttons on popup window
    BROWSER_URLS= { "monitor": "$MONITOR$/status/service?filter=unhandled&includeunhandledhosts=1",\
                    "hosts": "$MONITOR$/status/host?hostgroupid=1&state=1",\
                    "services": "$MONITOR$/status/service?state=1&state=2&state=3",\
                    "history": "$MONITOR$/event"}

    def init_HTTP(self):
        """
            things to do if HTTP is not initialized
        """
        GenericServer.init_HTTP(self)

        """
            # special Opsview treatment, transmit username and passwort for XML requests
            # http://docs.opsview.org/doku.php?id=opsview3.4:api
            # this is only necessary when accessing the API and expecting a XML answer
            self.HTTPheaders["xml"] = {"Content-Type":"text/xml", "X-Username":self.get_username(), "X-Password":self.get_password()}
        """
        self.session.headers.update({"Content-Type": "text/xml",
                                     "X-Username":self.get_username(),
                                     "X-Password":self.get_password()})


        # get cookie to access Opsview web interface to access Opsviews Nagios part
        if len(self.session.cookies) == 0:
            # put all necessary data into url string
            logindata = urllib.parse.urlencode({'login_username': self.get_username(),
                                                'login_password': self.get_password(),
                                                'back': '',
                                                'app': 'OPSVIEW',
                                                'login': 'Sign in',
                                                'noscript': '1'})

            # the following is necessary for Opsview servers
            # get cookie from login page via url retrieving as with other urls
            try:
                # login and get cookie
                self.FetchURL(self.monitor_url + "/login", cgi_data=logindata, giveback='raw')
            except:

                import traceback
                traceback.print_exc(file=sys.stdout)

                self.Error(sys.exc_info())


    def init_config(self):
        """
            dummy init_config, called at thread start, not really needed here, just omit extra properties
        """
        pass


    def get_start_end(self, host):
        """
        for GUI to get actual downtime start and end from server - they may vary so it's better to get
        directly from web interface
        """
        try:
            result = self.FetchURL(self.monitor_cgi_url + "/cmd.cgi?" + urllib.parse.urlencode({"cmd_typ":"55", "host":host}))
            html = result.result
            start_time = dict(result.result.find(attrs={"name":"starttime"}).attrs)["value"]
            end_time = dict(result.result.find(attrs={"name":"endtime"}).attrs)["value"]
            # give values back as tuple
            return start_time, end_time
        except:
            self.Error(sys.exc_info())
            return "n/a", "n/a"


    def _set_downtime(self, host, service, author, comment, fixed, start_time, end_time, hours, minutes):
        # get action url for opsview downtime form
        if service == "":
            # host
            cgi_data = urllib.parse.urlencode({"cmd_typ":"55", "host":host})
        else:
            # service
            cgi_data = urllib.parse.urlencode({"cmd_typ":"56", "host":host, "service":service})
        url = self.monitor_cgi_url + "/cmd.cgi"
        result = self.FetchURL(url, giveback="raw", cgi_data=cgi_data)
        html = result.result
        # which opsview form action to call
        action = html.split('" enctype="multipart/form-data">')[0].split('action="')[-1]
        # this time cgi_data does not get encoded because it will be submitted via multipart
        # to build value for hidden form field old cgi_data is used
        cgi_data = { "from" : url + "?" + cgi_data, "comment": comment, "starttime": start_time, "endtime": end_time }
        self.FetchURL(self.monitor_url + action, giveback="raw", cgi_data=cgi_data)


    def _set_submit_check_result(self, host, service, state, comment, check_output, performance_data):
        """
        worker for submitting check result
        """
        # decision about host or service - they have different URLs
        if service == "":
            # host - here Opsview uses the plain oldschool Nagios way of CGI
            url = self.monitor_cgi_url + "/cmd.cgi"
            cgi_data = urllib.parse.urlencode({"cmd_typ":"87", "cmd_mod":"2", "host":host,\
                                         "plugin_state":{"up":"0", "down":"1", "unreachable":"2"}[state], "plugin_output":check_output,\
                                         "performance_data":performance_data, "btnSubmit":"Commit"})
            self.FetchURL(url, giveback="raw", cgi_data=cgi_data)

        if service != "":
            # service @ host - here Opsview brews something own
            url = self.monitor_url + "/state/service/" + self.hosts[host].services[service].service_object_id + "/change"
            cgi_data = urllib.parse.urlencode({"state":{"ok":"0", "warning":"1", "critical":"2", "unknown":"3"}[state],\
                                         "comment":comment, "submit":"Commit"})
            # running remote cgi command
            self.FetchURL(url, giveback="raw", cgi_data=cgi_data)


    def _get_status(self):
        """
            Get status from Opsview Server
        """
        # following http://docs.opsview.org/doku.php?id=opsview3.4:api to get ALL services in ALL states except OK
        # because we filter them out later
        # the API seems not to let hosts information directly, we hope to get it from service informations
        try:
            result = self.FetchURL(self.monitor_url + "/api/status/service?state=1&state=2&state=3", giveback="xml")
            xmlobj, error = result.result, result.error
            if error != "": return Result(result=xmlobj, error=copy.deepcopy(error))

            for host in xmlobj.data.findAll('list'):
                # host
                hostdict = host.attrs
                self.new_hosts[str(hostdict["name"])] = GenericHost()
                self.new_hosts[str(hostdict["name"])].name = str(hostdict["name"])
                self.new_hosts[str(hostdict["name"])].server = self.name
                # states come in lower case from Opsview
                self.new_hosts[str(hostdict["name"])].status = str(hostdict["state"].upper())
                self.new_hosts[str(hostdict["name"])].status_type = str(hostdict["state_type"])
                self.new_hosts[str(hostdict["name"])].last_check = str(hostdict["last_check"])
                self.new_hosts[str(hostdict["name"])].duration = HumanReadableDurationFromSeconds(hostdict["state_duration"])
                self.new_hosts[str(hostdict["name"])].attempt = str(hostdict["current_check_attempt"])+ "/" + str(hostdict["max_check_attempts"])
                self.new_hosts[str(hostdict["name"])].status_information = str(hostdict["output"].replace("\n", " "))
                # if host is in downtime add it to known maintained hosts
                if hostdict["downtime"] == "2":
                    self.new_hosts[str(hostdict["name"])].scheduled_downtime = True
                if 'acknowledged' in hostdict:
                    self.new_hosts[str(hostdict["name"])].acknowledged = True
                if 'flapping' in hostdict:
                    self.new_hosts[str(hostdict["name"])].flapping = True

                #services
                for service in host.findAll("services"):
                    servicedict = service.attrs
                    self.new_hosts[str(hostdict["name"])].services[str(servicedict["name"])] = OpsviewService()
                    self.new_hosts[str(hostdict["name"])].services[str(servicedict["name"])].host = str(hostdict["name"])
                    self.new_hosts[str(hostdict["name"])].services[str(servicedict["name"])].name = str(servicedict["name"])
                    self.new_hosts[str(hostdict["name"])].services[str(servicedict["name"])].server = self.name
                    # states come in lower case from Opsview
                    self.new_hosts[str(hostdict["name"])].services[str(servicedict["name"])].status = str(servicedict["state"].upper())
                    self.new_hosts[str(hostdict["name"])].services[str(servicedict["name"])].status_type = str(servicedict["state_type"])
                    self.new_hosts[str(hostdict["name"])].services[str(servicedict["name"])].last_check = str(servicedict["last_check"])
                    self.new_hosts[str(hostdict["name"])].services[str(servicedict["name"])].duration = HumanReadableDurationFromSeconds(servicedict["state_duration"])
                    self.new_hosts[str(hostdict["name"])].services[str(servicedict["name"])].attempt = str(servicedict["current_check_attempt"])+ "/" + str(servicedict["max_check_attempts"])
                    self.new_hosts[str(hostdict["name"])].services[str(servicedict["name"])].status_information= str(servicedict["output"].replace("\n", " "))
                    if servicedict['downtime'] == "2":
                        self.new_hosts[str(hostdict["name"])].services[str(servicedict["name"])].scheduled_downtime = True
                    if 'acknowledged' in servicedict:
                        self.new_hosts[str(hostdict["name"])].services[str(servicedict["name"])].acknowledged = True
                    if 'flapping' in servicedict:
                        self.new_hosts[str(hostdict["name"])].services[str(servicedict["name"])].flapping = True

                    # extra opsview id for service, needed for submitting check results
                    self.new_hosts[str(str(hostdict["name"]))].services[str(str(servicedict["name"]))].service_object_id = str(servicedict["service_object_id"])
                del servicedict
                del hostdict

        except:

            import traceback
            traceback.print_exc(file=sys.stdout)

            # set checking flag back to False
            self.isChecking = False
            result, error = self.Error(sys.exc_info())
            return Result(result=result, error=error)

        #dummy return in case all is OK
        return Result()


    def open_monitor(self, host, service):
        webbrowser.open('%s/status/service?host=%s' % (self.monitor_url, host))

