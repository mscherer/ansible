# Based on local.py (c) 2012, Michael DeHaan <michael.dehaan@gmail.com>
# Based on chroot.py (c) 2013, Maykel Moya <mmoya@speedyrails.com>
# (c) 2014, Michael Scherer <misc@zarb.org>
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.

import distutils.spawn
import os
import pipes
import shlex
import subprocess
from ansible import errors
from ansible import utils
from ansible.callbacks import vvv
from ansible.constants import get_config, p as cfg, DEFAULT_SUDO_EXE
import docker

class Connection(object):
    ''' Local docker based connections '''

    def _search_executable(self, executable):
        cmd = distutils.spawn.find_executable(executable)
        if not cmd:
            raise errors.AnsibleError("%s command not found in PATH") % executable
        return cmd

    def __init__(self, runner, host, port, *args, **kwargs):
        self.docker_id = host
        self.has_pipelining = False

        self.cmd = self._search_executable('nsenter')

        self.runner = runner
        self.host = host
        # port is unused, since this is local
        self.port = port
        self.pid = None

    def connect(self, port=None):
        ''' connect to the daemon and get the PID for nsenter '''
        c = docker.Client()
        self.pid = c.inspect_container(self.docker_id)['State']['Pid']

        return self

    def _generate_cmd(self, cmd, sudo_user, sudoable, executable, su, su_user):

        if self.runner.sudo and sudoable:
            shcmd, prompt, success_key = utils.make_sudo_cmd(self.runner.sudo_exe, sudo_user, executable, cmd)
        elif self.runner.su and su:
            shcmd, prompt, success_key = utils.make_su_cmd(su_user, executable, cmd)
        else:
            if executable:
                shcmd = '%s -c %s' % (executable, pipes.quote(cmd))
            else:
                shcmd = cmd

        return '%s --mount --uts --ipc --net --pid --target %s -- %s' % (self.cmd, self.pid, shcmd)

    def exec_command(self, cmd, tmp_path, sudo_user=None, sudoable=False, executable='/bin/sh', in_data=None, su=None, su_user=None):
        ''' run a command in the docker namespace '''

        if in_data:
            raise errors.AnsibleError("Internal Error: this module does not support optimized module pipelining")

        local_cmd = self._generate_cmd(cmd, sudo_user, sudoable, executable, su, su_user)
        if get_config(cfg, 'nsenter', 'sudo', None, True, boolean=True):
            local_cmd = '%s %s' % (DEFAULT_SUDO_EXE, local_cmd)

        vvv("EXEC %s" % (local_cmd), host=self.docker_id)
        p = subprocess.Popen(shlex.split(local_cmd), cwd=self.runner.basedir,
                             stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        stdout, stderr = p.communicate()
        return (p.returncode, '', stdout, stderr)

    def _normalize_path(self, path, prefix):
        if not path.startswith(os.path.sep):
            path = os.path.join(os.path.sep, path)
        normpath = os.path.normpath(path)
        return os.path.join(prefix, normpath[1:])

    def put_file(self, in_path, out_path):
        ''' transfer a file from local to the namespace '''

        out_path = self._normalize_path(out_path, '/')
        vvv("PUT %s TO %s" % (in_path, out_path), host=self.docker_id)

        local_cmd = [self.cmd, '--mount', '--uts', '--ipc', '--net', '--pid', '--target', str(self.pid), '--', 'tee', out_path]
        if get_config(cfg, 'nsenter', 'sudo', None, True, boolean=True):
            local_cmd.insert(0, DEFAULT_SUDO_EXE)
        vvv("EXEC %s" % (local_cmd), host=self.docker_id)

        p = subprocess.Popen(local_cmd, cwd=self.runner.basedir,
                             stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = p.communicate(open(in_path, 'rb').read())

    def fetch_file(self, in_path, out_path):
        ''' fetch a file from docker to local '''

        in_path = self._normalize_path(in_path, '/')
        vvv("FETCH %s TO %s" % (in_path, out_path), host=self.docker_id)

        local_cmd = [self.cmd, '--mount', '--uts', '--ipc', '--net', '--pid', '--target', str(self.pid), '--', 'cat', in_path]
        if get_config(cfg, 'nsenter', 'sudo', None, True, boolean=True):
            local_cmd.insert(0, DEFAULT_SUDO_EXE)
        vvv("EXEC %s" % (local_cmd), host=self.docker_id)

        p = subprocess.Popen(local_cmd, cwd=self.runner.basedir,
                             stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = p.communicate()
        open(out_path, 'wb').write(stdout)

    def close(self):
        ''' terminate the connection; nothing to do here '''
        pass
