#!/usr/bin/env python

# Firelet - Distributed firewall management.
# Copyright (C) 2010 Federico Ceratto
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import logging as log
from beaker.middleware import SessionMiddleware
import bottle
from bottle import route, send_file, run, view, request
from bottle import debug as bottle_debug
from collections import defaultdict
from datetime import datetime
from subprocess import Popen, PIPE
from sys import argv, exit
from time import time, sleep, localtime

from confreader import ConfReader
import mailer
from flcore import Alert, GitFireSet, DemoGitFireSet, Users, clean
from flmap import draw_png_map, draw_svg_map
from flutils import flag

from bottle import HTTPResponse, HTTPError

class LoggedHTTPError(bottle.HTTPResponse):
    """ Used to generate an error page """
    def __init__(self, code=500, output='Unknown Error', exception=None, traceback=None, header=None):
        super(bottle.HTTPError, self).__init__(output, code, header)
        log.debug(            """Internal error '%s':\n  Output: %s\n  Header: %s\n  %s--- End of traceback ---"""% (exception, output, header, traceback))


bottle.HTTPError = LoggedHTTPError

#TODO: HG, H, N, Rule, Service creation
#TODO: Rule up/down move
#TODO: say() as a custom log target
#TODO: full rule checking upon Save
#TODO: move fireset editing in flcore
#TODO: setup three roles
#TODO: display only login form to unauth users
#TODO: store a local copy of the deployed confs
#              - compare in with the fetched conf
#              - show it on the webapp

msg_list = []

def say(s, level='info'):
    """level can be: info, warning, alert"""
    if level == 'error':
        level = 'alert'
    log.debug(s)
    ts = datetime.now().strftime("%H:%M:%S")
    msg_list.append((level, ts, s))
    if len(msg_list) > 10:
        msg_list.pop(0)


def pg(name, default=''):
    """Retrieve an element from a POST request"""
    s = request.POST.get(name, default)[:64]
    return clean(s).strip()

def int_pg(name, default=None):
    """Retrieve an element from a POST request and returns it as an integer"""
    v = request.POST.get(name, default)
    if v == '':
        return None
    try:
        return int(v)
    except:
        raise Exception, "Expected int as POST parameter, got string: '%s'." % v

def pcheckbox(name):
    """Retrieve a checkbox status from a POST request generated by serializeArray() and returns '0' or '1' """
    if name in request.POST:
        return '1'
    return '0'

# # #  web services  # # #


# #  authentication  # #

def _require(role='readonly'):
    """Ensure the user has the required role (or higher).
    Order: admin > editor > readonly
    """
    m = {'admin': 15, 'editor': 10, 'readonly': 5}
    s = bottle.request.environ.get('beaker.session')
    if not s:
        say("User needs to be authenticated.", level="warning") #TODO: not really explanatory in a multiuser session.
        raise Alert, "User needs to be authenticated."
    myrole = s.get('role', '')
    if m[myrole] >= m[role]:
        return
    say("An account with '%s' level or higher is required." % repr(role))
    raise Exception

@bottle.route('/login', method='POST')
def login():
    """ """
    s = bottle.request.environ.get('beaker.session')
    if 'username' in s:  # user is authenticated <--> username is set
        say("Already logged in as \"%s\"." % s['username'])
        return {'logged_in': True}
    user = pg('user', '')
    pwd = pg('pwd', '')
    try:
        users.validate(user, pwd)
        role = users._users[user][0]
        say("User %s with role %s logged in." % (user, role), level="success")
        s['username'] = user
        s['role'] = role
        s = bottle.request.environ.get('beaker.session')
        s.save()
        bottle.redirect('')
    except (Alert, AssertionError), e:
        say("Login denied for \"%s\": %s" % (user, e), level="warning")
        log.debug("Login denied for \"%s\": %s" % (user, e))
        bottle.redirect('')

@bottle.route('/logout')
def logout():
    s = bottle.request.environ.get('beaker.session')
    u = s.get('username', None)
    if u:
        say('User %s logged out.' % u)
    s.delete()
    bottle.redirect('')



#
#class WebApp(object):
#
#def __init__(self, conf):
#    self.conf = conf
#    self.messages = []

@bottle.route('/messages')
@view('messages')
def messages():
    return dict(messages=msg_list)

@bottle.route('/')
@view('index')
def index():
    s = bottle.request.environ.get('beaker.session')
    logged_in = True if s and 'username' in s else False

    try:
        title = conf.title
    except:
        title = 'test'
    return dict(msg=None, title=title, logged_in=logged_in)

# #  tables interaction  # #
#
# GETs are used to list all table contents
# POSTs are used to make changes or to populate editing forms
# POST "verbs" are sent using the "action" key, and the "rid" key
# specifies the target:
#   - delete
#   - moveup/movedown/enable/disable   see ruleset()
#   - edit: updates an element if rid is not null, otherwise creates
#             a new one

@bottle.route('/ruleset')
@view('ruleset')
def ruleset():
    _require()
    return dict(rules=enumerate(fs.rules))

@bottle.route('/ruleset', method='POST')
def ruleset():
    _require('editor')
    action = pg('action', '')
    name = pg('name', '')
    rid = int_pg('rid')

    if action == 'delete':
        try:
            assert rid, "Item number not provided"
            fs.delete('rules', rid)
            say("Rule %d deleted." % rid, level="success")
            return
        except Exception, e:
            say("Unable to delete rule %s - %s" % (name, e), level="alert")
            abort(500)
    elif action == 'moveup':
        try:
            fs.rule_moveup(rid)
        except Exception, e:
            say("Cannot move rule %d up." % rid)
    elif action == 'movedown':
        try:
            fs.rule_movedown(rid)
        except Exception, e:
            say("Cannot move rule %d down." % rid)
    elif action == 'disable':
        fs.rule_disable(rid)
        say("Rule %d disabled." % rid)
    elif action == 'enable':
        fs.rule_enable(rid)
        say("Rule %d enabled." % rid)


@bottle.route('/hostgroups')
@view('hostgroups')
def hostgroups():
    _require()
    return dict(hostgroups=enumerate(fs.hostgroups))

@bottle.route('/hostgroups', method='POST')
def hostgroups():
    _require('editor')
    action = pg('action', '')
    rid = int_pg('rid')
    if action == 'delete':
        try:
            assert rid, "Item number not provided"

            fs.delete('hostgroups', rid)
            say("Host Group %s deleted." % rid, level="success")
            return
        except Exception, e:
            say("Unable to delete %s - %s" % (rid, e), level="alert")
            abort(500)

def ack(s=None):
    """Acknowledge successful form processing and returns ajax confirmation."""
    if s:
        say(s, level="success")
    return {'ok': True}

@bottle.route('/hosts')
@view('hosts')
def hosts():
    _require()
    return dict(hosts=enumerate(fs.hosts))

@bottle.route('/hosts', method='POST')
def hosts():
    _require('editor')
    action = pg('action', '')
    rid = int_pg('rid')
    if action == 'delete':
        try:
            fs.delete('hosts', rid)
            say("Host %s deleted." % rid, level="success")
            return
        except Exception, e:
            say("Unable to delete %s - %s" % (rid, e), level="alert")
            abort(500)
    elif action == 'save':
        d = {}
        for f in ('hostname', 'iface', 'ip_addr', 'masklen'):
            d[f] = pg(f)
        for f in ('local_fw', 'network_fw', 'mng'):
            d[f] = pcheckbox(f)
        r = pg('routed').split(',')
        r = list(set(r)) # remove duplicate routed nets
        d['routed'] = r
        if rid == None:     # new host
            try:
                fs.hosts.add(d)
                return ack('Host %s added.' % d['hostname'])
            except Alert, e:
                say('Unable to add %s.' % d['hostname'], level="alert")
                return {'ok': False, 'hostname':'Must start with "test"'} #TODO: complete this
        else:   # update host
            try:
                fs.hosts.update(d, rid=rid, token=pg('token'))
                return ack('Host updated.')
            except Alert, e:
                say('Unable to edit %s.' % hostname, level="alert")
                return {'ok': False, 'hostname':'Must start with "test"'} #TODO: complete this
    elif action == 'fetch':
        try:
            h = fs.fetch('hosts', rid)
            d = h.attr_dict()
            for x in ('local_fw', 'network_fw', 'mng'):
                d[x] = int(d[x])
            return d
        except Alert, e:
            say('TODO')
    else:
        log.error('Unknown action requested: "%s"' % action)


@bottle.route('/net_names', method='POST')
def net_names():
    _require()
    nn = [n.name for n in fs.networks]
    return dict(net_names=nn)

@bottle.route('/networks')
@view('networks')
def networks():
    _require()
    return dict(networks=enumerate(fs.networks))

@bottle.route('/networks', method='POST')
def networks():
    _require('editor')
    action = pg('action', '')
    rid = int_pg('rid')
    if action == 'delete':
        try:
            assert rid, "Item number not provided"
            fs.delete('networks', rid)
            say("Network %s deleted." % rid, level="success")
            return
        except Exception, e:
            say("Unable to delete %s - %s" % (rid, e), level="alert")
            abort(500)

    #TODO: finish the following part
    elif action == 'save':
        d = {}
        for f in ('hostname', 'iface', 'ip_addr', 'masklen'):
            d[f] = pg(f)
        for f in ('local_fw', 'network_fw', 'mng'):
            d[f] = pcheckbox(f)
        r = pg('routed').split(',')
        r = list(set(r)) # remove duplicate routed nets
        d['routed'] = r
        if rid == None:     # new host
            try:
                fs.hosts.add(d)
                return ack('Host %s added.' % d['hostname'])
            except Alert, e:
                say('Unable to add %s.' % d['hostname'], level="alert")
                return {'ok': False, 'hostname':'Must start with "test"'} #TODO: complete this
        else:   # update host
            try:
                fs.hosts.update(d, rid=rid, token=pg('token'))
                return ack('Host updated.')
            except Alert, e:
                say('Unable to edit %s.' % hostname, level="alert")
                return {'ok': False, 'hostname':'Must start with "test"'} #TODO: complete this
    elif action == 'fetch':
        try:
            h = fs.fetch('hosts', rid)
            d = h.attr_dict()
            for x in ('local_fw', 'network_fw', 'mng'):
                d[x] = int(d[x])
            return d
        except Alert, e:
            say('TODO')
    else:
        log.error('Unknown action requested: "%s"' % action)




@bottle.route('/services')
@view('services')
def services():
    _require()
    return dict(services=enumerate(fs.services))

@bottle.route('/services', method='POST')
def services():
    _require('editor')
    action = pg('action', '')
    rid = int_pg('rid')
    if action == 'delete':
        try:
            fs.delete('services', rid)
            say("Service %s deleted." % rid, level="success")
            return
        except Exception, e:
            say("Unable to delete %s - %s" % (rid, e), level="alert")
            abort(500)


# management commands

@bottle.route('/manage')
@view('manage')
def manage():
    _require()
    s = bottle.request.environ.get('beaker.session')
    myrole = s.get('role', '')
    cd = True if myrole == 'admin' else False
    return dict(can_deploy=cd)

@bottle.route('/save_needed')
def save_needed():
    _require()
    return {'sn': fs.save_needed()}

@bottle.route('/save', method='POST')
def savebtn():
    _require()
    msg = pg('msg', '')
    if not fs.save_needed():
        say('Save not needed.', level="warning")
        return
    say('Saving configuration...')
    say("Commit msg: \"%s\"" % msg)
    saved = fs.save(msg)
    if saved:
        say('Configuration saved.', level="success")
        return

@bottle.route('/reset', method='POST')
def resetbtn():
    _require()
    if not fs.save_needed():
        say('Reset not needed.', level="warning")
        return
    say("Resetting configuration changes...")
    fs.reset()
    say('Configuration reset.', level="success")
    return

@bottle.route('/check', method='POST')
def checkbtn():
    _require()
    say('Configuration check started...')
    try:
#        import time
#        time.sleep(1)
        diff_table = fs.check()
    except Alert, e:
        say("Check failed: %s" % e,  level="alert")
        return dict(diff_table="Check failed: %s" % e)
    except Exception, e:
        import traceback
        log.debug(traceback.format_exc())
        return
    say('Configuration check successful.', level="success")
    return dict(diff_table=diff_table)

@bottle.route('/deploy', method='POST')
def deploybtn():
    _require('admin')
    say('Configuration deployment started...')
    say('Compiling firewall rules...')
    try:
        fs.deploy()
    except Exception, e:
        say("Compilation failed: %s" % e,  level="alert")
        return
    say('Configuration deployed.', level="success")
    return

@bottle.route('/version_list')
@view('version_list')
def version_list():
    _require()
    li = fs.version_list()
    return dict(version_list=li)

@bottle.route('/version_diff', method='POST')
@view('version_diff')
def version_diff():
    _require()
    cid = pg('commit_id') #TODO validate cid?
    li = fs.version_diff(cid)
    if li:
        return dict(li=li)
    return dict(li=(('(No changes.)', 'title')))

@bottle.route('/rollback', method='POST')
def rollback():
    _require('admin')
    cid = pg('commit_id') #TODO validate cid?
    fs.rollback(cid)
    say("Configuration rolled back.")
    return

# serving static files

@bottle.route('/static/:filename#[a-zA-Z0-9_\.?\/?]+#')
def static_file(filename):
    _require()
    bottle.response.headers['Cache-Control'] = 'max-age=3600, public'
    if filename == '/jquery-ui.js':
        send_file('/usr/share/javascript/jquery-ui/jquery-ui.js') #TODO: support other distros
    elif filename == 'jquery.min.js':
        send_file('/usr/share/javascript/jquery/jquery.min.js', root='/')
    elif filename == 'jquery-ui.custom.css': #TODO: support version change
        send_file('/usr/share/javascript/jquery-ui/css/smoothness/jquery-ui-1.7.2.custom.css')
    else:
        send_file(filename, root='static')

@bottle.route('/favicon.ico')
def favicon():
    send_file('favicon.ico', root='static')

@bottle.route('/map') #FIXME: the SVG map is not shown inside the jQuery tab.
def flmap():
    return """<img src="map.png" width="700px" style="margin: 10px">"""

@bottle.route('/map.png')
def flmap_png():
    bottle.response.content_type = 'image/png'
    return draw_png_map(fs)

@bottle.route('/svgmap')
def flmap_svg():
    bottle.response.content_type = 'image/svg+xml'
    return draw_svg_map(fs)

#TODO: provide PNG fallback for browser without SVG support?
#TODO: html links in the SVG map

def main():
    global conf

    try:
        fn = argv[1:]
        if '-D' in fn: fn.remove('-D')
        if not fn: fn = 'firelet.ini'
        conf = ConfReader(fn=fn)
    except Exception, e:
        log.error("Exception %s while reading configuration file '%s'" % (e, fn))
        exit(1)

    # logging

    if '-D' in argv:
        debug_mode = True
        log.basicConfig(level=log.DEBUG,
                        format='%(asctime)s %(levelname)-8s %(message)s',
                        datefmt='%a, %d %b %Y %H:%M:%S')
        log.debug("Debug mode")
        bottle.debug(True)
        say("Firelet started in debug mode.", level="success")
        bottle_debug(True)
        reload = False
    else:
        debug_mode = False
        log.basicConfig(level=log.INFO,
                    format='%(asctime)s %(levelname)-8s %(message)s',
                    datefmt='%a, %d %b %Y %H:%M:%S',
                    filename=conf.logfile,
                    filemode='w')
        reload = False



        say("Firelet started.", level="success")

    if conf.demo_mode == 'False':
        globals()['fs'] = GitFireSet()
        say("Configuration loaded.")
        say("%d hosts, %d rules, %d networks loaded." % (len(fs.hosts), len(fs.rules), len(fs.networks)))
        globals()['users'] = Users(d='firewall')
    elif conf.demo_mode == 'True':
        globals()['fs'] = DemoGitFireSet()
        say("Demo mode.")
        say("%d hosts, %d rules, %d networks loaded." % (len(fs.hosts), len(fs.rules), len(fs.networks)))
        globals()['users'] = Users()
#        reload = True



    session_opts = {
        'session.type': 'cookie',
        'session.validate_key': True,
    }
    app = bottle.default_app()
    app = SessionMiddleware(app, session_opts)

    run(app=app, host=conf.listen_address, port=conf.listen_port, reloader=reload)


if __name__ == "__main__":
    main()












