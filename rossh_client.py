#!/usr/bin/env python3

'''
@file rossh_server.py
@author Zizheng Guo (https://github.com/gzz2000)
@brief The RoSSH client that wraps SSH and communicate with RoSSH transient server at remote.
'''

import os
import sys
import pty
import select
import signal
import time

from rossh_common import \
    gen_term_id, \
    write_to, \
    write_to_master_fd, \
    forward_window_resize, \
    raw_tty

banner = '''\
   ___       __________ __
  / _ \___  / __/ __/ // /
 / , _/ _ \_\ \_\ \/ _  /
/_/|_|\___/___/___/_//_/

Robost SSH (RoSSH) version 1.0 by Zizheng Guo
GPL License.
'''

stdin_fileno = sys.stdin.fileno()
stdout_fileno = sys.stdout.fileno()

class ConnectionError(Exception):
    pass

class ClientSession:
    def __init__(self, term_id, args):
        self.term_id = term_id
        self.args = args

    def init_connection(self, master_fd):
        with raw_tty():
            fds = [master_fd, stdin_fileno]
            ssh_established = False
            
            while True:
                rfds, _, _ = select.select(fds, [], [])

                if master_fd in rfds:
                    data = os.read(master_fd, 1024)

                    if not data:
                        raise ConnectionError('Master fd closed')

                    # before connection establishes, connect the SSH terminal
                    # to stdin/stdout, for entering password, etc.
                    
                    if data.find(b'\x1b+SSHOK') >= 0:
                        write_to(stdout_fileno, data[:data.find(b'\x1b+SSHOK')])
                        ssh_established = True

                    if not ssh_established:
                        write_to(stdout_fileno, data)
                        
                    if data.find(b'\x1b+CONN:FL:CLI') >= 0:
                        print('[RoSSH] Copying RoSSH to remote server ~/.rossh ...')
                        write_to(master_fd, b'mkdir -p ~/.rossh\n')
                        write_to(master_fd, b'chmod go-w ~/.rossh\n')
                        write_to(master_fd, b'cd ~/.rossh\n')

                        curdir = os.path.dirname(os.path.abspath(__file__))
                        for fname in ['rossh_client.py', 'rossh_server.py', 'rossh_common.py']:
                            write_to(master_fd, b'cat <<EOF > ' + bytes(fname, encoding='utf-8') + b'\n')
                            with open(os.path.join(curdir, fname), 'rb') as f:
                                write_to(master_fd, f.read())
                            write_to(master_fd, b'EOF\n')

                        write_to(master_fd, b'chmod go-w,+x *\n')
                        write_to(master_fd, b'cd ~\n')
                        write_to(master_fd, bytes('~/.rossh/rossh_server.py -t %s\n' % self.term_id, encoding='utf-8'))

                    # It would be a good exam problem to ask: why must we separate the /usr/ with bin/env?
                    # (Hint: consider remote echo of this file when installing)
                    if data.find(b'/usr/' + b'bin/env:') >= 0 and data.find(b'No such file or directory') >= 0:
                        print('[RoSSH] No Python 3 found at remote server. You must install one to use RoSSH.')
                        raise ConnectionError('No python 3 found.')

                    if data.find(b'\x1b+CONN:S') >= 0:
                        write_to(stdout_fileno, data[data.find(b'\x1b+CONN:S') + len(b'\x1b+CONN:S'):])
                        break

                if stdin_fileno in rfds:
                    data = os.read(stdin_fileno, 1024)

                    if not ssh_established:
                        write_to(master_fd, data)

    def connect(self):
        args = self.args + ['-t', '(echo -e "\x1b+SSHOK" && ~/.rossh/rossh_server.py -t %s) || (echo -e "\x1b+CONN:FL:CLI" && /bin/bash)' % self.term_id]
        print('[RoSSH] Connecting to: ' + ' '.join(self.args) + ' -t <rossh_server>')
        ssh_pid, master_fd = pty.fork()
        if ssh_pid == 0:
            os.execvp(args[0], args)

        # init connection.
        try:
            self.init_connection(master_fd)
            
        except (ConnectionError, OSError) as e:
            if isinstance(e, OSError):
                print('[RoSSH] Connection failed.')
            else:
                print('[RoSSH] Connection failed: %s' % e)
            
            # cleanup
            os.close(master_fd)
            os.kill(ssh_pid, signal.SIGINT)
            os.waitpid(ssh_pid, 0)
            return None, None

        return ssh_pid, master_fd

    def attach(self):
        session_created = False
        
        while True:
            ssh_pid, master_fd = self.connect()
            if master_fd is None:
                print('[RoSSH] Press any key to retry. Ctrl-C to give up.')
                with raw_tty():
                    data = os.read(stdin_fileno, 1024)
                if data == b'\x03':
                    if session_created:
                        print('[RoSSH] The orphaned remote session would be killed the next time you log into this server from this client.')
                    return
                continue

            session_created = True

            try:
                with raw_tty(), forward_window_resize(master_fd, indirect=False):
                    fds = [master_fd, stdin_fileno]

                    while True:
                        rfds, _, _ = select.select(fds, [], [])

                        if stdin_fileno in rfds:
                            data = os.read(stdin_fileno, 1024)
                            write_to(master_fd, data)

                        if master_fd in rfds:
                            data = os.read(master_fd, 1024)

                            if not data:
                                print('\r\n[RoSSH] SSH disconnected. reconnecting...\r')
                                break

                            # before connection establishes, connect the SSH terminal
                            # to stdin/stdout, for entering password, etc.

                            if data.find(b'\x1b+CONN:E') >= 0:
                                write_to(stdout_fileno, data[:data.find(b'\x1b+CONN:E')])
                                print('\r[RoSSH] Exited gracefully.\r')
                                return

                            write_to(stdout_fileno, data)
                            
            except IOError as e:
                print('\r\n[RoSSH] SSH disconnected. reconnecting...\r')
                continue
            
            finally:
                os.close(master_fd)
                os.kill(ssh_pid, signal.SIGINT)
                os.waitpid(ssh_pid, 0)

if __name__ == '__main__':
    print(banner)
    term_id = 'test' # gen_term_id()
    args = [*sys.argv]
    args[0] = 'ssh'
    sess = ClientSession(term_id, args)
    sess.attach()