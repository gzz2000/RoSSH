#!/usr/bin/env python3

'''
@file rossh_server.py
@author Zizheng Guo (https://github.com/gzz2000)
@brief The code that would run on the remote server. It creates and manages shells behind pseudo terminals.
'''

import pty
import os
import sys
import argparse
import signal
import select
import shutil
import atexit

from rossh_common import \
    rossh_version_index, \
    build_ctlseq, \
    write_to, \
    write_to_master_fd, \
    change_signal, \
    forward_window_resize, \
    raw_tty, \
    set_sync_output

SHELL = os.environ.get('SHELL', 'sh')

parser = argparse.ArgumentParser(
    description='RoSSH server script that creates and manages shells behind pseudo terminals.')

parser.add_argument('-V', dest='client_version', type=int, default=0,
                    help='Check that the server version is greater than the client version specified.')
parser.add_argument('-t', dest='term', type=str, required=True,
                    help='Terminal id to create or attach to.')
parser.add_argument('--kill', type=str, nargs='*',
                    help='Id of orphaned terminals to kill')

class Session:
    def __init__(self, term_id):
        self.RoSSH_DIRNAME = 'rossh.%s' % term_id
        self.RoSSH_DIR = '/tmp/%s' % self.RoSSH_DIRNAME
        self.RoSSH_SESS_PID_PATH = '%s/session.pid' % self.RoSSH_DIR
        self.RoSSH_CONN_PID_PATH = '%s/connection.pid' % self.RoSSH_DIR
        self.RoSSH_SOCK_PATH = '%s/auth.sock' % self.RoSSH_DIR
        self.RoSSH_INPUT_PIPE_PATH = '%s/input' % self.RoSSH_DIR
        self.RoSSH_OUTPUT_PIPE_PATH = '%s/output' % self.RoSSH_DIR

    def copy_to_daemon(self, master_fd):
        f_input = open(self.RoSSH_INPUT_PIPE_PATH, 'r')
        f_input_fileno = f_input.fileno()
        f_output = open(self.RoSSH_OUTPUT_PIPE_PATH, 'w')
        f_output_fileno = f_output.fileno()

        try:
            fds = [master_fd, f_input_fileno]
            while True:
                rfds, _, _ = select.select(fds, [], [])

                if master_fd in rfds:
                    data = os.read(master_fd, 1024)
                    if not data:
                        break
                    else:
                        write_to(f_output_fileno, data)

                if f_input_fileno in rfds:
                    data = os.read(f_input_fileno, 1024)
                    if not data:
                        # an input EOF may actually means the connection
                        # has broken and would reconnect later. so we just
                        # reopen the pipe.
                        fds.remove(f_input_fileno)
                        f_input.close()
                        f_input = open(self.RoSSH_INPUT_PIPE_PATH, 'r')
                        f_input_fileno = f_input.fileno()
                        fds.append(f_input_fileno)
                    else:
                        write_to_master_fd(master_fd, data)
        finally:
            f_output.close()
            f_input.close()

    def create_session_daemon(self):
        os.mkfifo(self.RoSSH_INPUT_PIPE_PATH)
        os.mkfifo(self.RoSSH_OUTPUT_PIPE_PATH)
        
        child_pid = os.fork()
        if child_pid != 0:
            with open(self.RoSSH_SESS_PID_PATH, 'w') as f:
                f.write(str(child_pid))
            return
        
        # below runs in the session daemon, and exits.
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        if 'SSH_AUTH_SOCK' in os.environ:
            os.environ['SSH_AUTH_SOCK'] = self.RoSSH_SOCK_PATH

        # create the shell
        # FROM pty.spawn source code at https://github.com/python/cpython/blob/3.9/Lib/pty.py#L151
        sys.stdout.write('[RoSSH session] created a new shell: ' +
                         SHELL + '.\n')

        shell_pid, master_fd = pty.fork()
        if shell_pid == 0:
            signal.signal(signal.SIGHUP, signal.SIG_DFL)
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            os.execvp(SHELL, [SHELL])

        def sigterm_handler(signum, frame):
            try:
                os.kill(shell_pid, signal.SIGHUP)
            except ProcessLookupError:
                pass
            raise OSError('External termination received')
            
        with change_signal(signal.SIGTERM, sigterm_handler):
            try:
                self.copy_to_daemon(master_fd)
            except OSError:
                pass

        os.close(master_fd)
        retv = os.waitpid(shell_pid, 0)[1]
        # print('[RoSSH debug] shell exited.')

        # sys.stdout.write('\r[RoSSH session] shell exited with status %d.\r\n' % retv)
        sys.exit(0)

    def create_if_not_exists(self):
        if os.path.exists(self.RoSSH_DIR):
            return
        os.mkdir(self.RoSSH_DIR, 0o700)
        self.create_session_daemon()

    def attach(self):
        if os.path.exists(self.RoSSH_CONN_PID_PATH):
            # kill the previous connection.
            with open(self.RoSSH_CONN_PID_PATH) as f:
                old_pid = int(f.read())
            try:
                # different from SIGHUP to avoid file remove race condition
                os.kill(old_pid, signal.SIGINT)
            except ProcessLookupError:
                pass

        with open(self.RoSSH_CONN_PID_PATH, 'w') as f:
            f.write(str(os.getpid()))

        def onhup_delpid(signum, frame):
            try:
                os.unlink(self.RoSSH_CONN_PID_PATH)
            except FileNotFoundError:
                pass
            sys.stdout.write('\r[RoSSH conn] SIGHUP\r\n')
            sys.exit(1)

        signal.signal(signal.SIGHUP, onhup_delpid)
        
        rssh_input = open(self.RoSSH_INPUT_PIPE_PATH, 'w')
        rssh_input_fileno = rssh_input.fileno()
        rssh_output = open(self.RoSSH_OUTPUT_PIPE_PATH, 'r')
        rssh_output_fileno = rssh_output.fileno()
        stdin_fileno = sys.stdin.fileno()
        stdout_fileno = sys.stdout.fileno()
        set_sync_output(stdout_fileno)
        
        sys.stdout.write("[RoSSH conn] connected to session\n")
        write_to(stdout_fileno, build_ctlseq(b'CONN:S'))

        with raw_tty(), forward_window_resize(rssh_input_fileno, indirect=True):
            fds = [stdin_fileno, rssh_output_fileno]
            
            while True:
                rfds, _, _ = select.select(fds, [], [])
                
                if stdin_fileno in rfds:
                    data = os.read(stdin_fileno, 1024)
                    if not data:
                        sys.stdout.write("[RoSSH conn] unexpected input EOF\n")
                        sys.exit(1)
                    else:
                        write_to(rssh_input_fileno, data)

                if rssh_output_fileno in rfds:
                    data = os.read(rssh_output_fileno, 1024)
                    if not data:
                        # shell exited
                        break
                    else:
                        write_to(stdout_fileno, data)

        # session terminated. cleanup.
        shutil.rmtree(self.RoSSH_DIR)
        
        write_to(stdout_fileno, build_ctlseq(b'CONN:E'))
        sys.stdout.write("[RoSSH conn] session exited\n")

    def destroy_if_exists(self):
        if os.path.exists(self.RoSSH_DIR):
            if os.path.exists(self.RoSSH_CONN_PID_PATH):
                # kill the previous connection.
                with open(self.RoSSH_CONN_PID_PATH) as f:
                    old_pid = int(f.read())

                try:
                    os.kill(old_pid, signal.SIGHUP)
                except ProcessLookupError: pass

            if os.path.exists(self.RoSSH_SESS_PID_PATH):
                # kill the session
                with open(self.RoSSH_SESS_PID_PATH) as f:
                    old_pid = int(f.read())

                try:
                    os.kill(old_pid, signal.SIGTERM)
                except ProcessLookupError: pass

            shutil.rmtree(self.RoSSH_DIR)
            return True
        
        else:
            return False

if __name__ == '__main__':
    args = parser.parse_args()
    
    if rossh_version_index < args.client_version:
        print(build_ctlseq('CONN:FL:VER:SERVER_UPDATE', output_str=True),
              flush=True)
        sys.exit(1)
    if rossh_version_index > args.client_version:
        print(build_ctlseq('CONN:FL:VER:CLIENT_TOOOLD', output_str=True),
              flush=True)
        sys.exit(1)

    for oid in (args.kill or []):
        sess = Session(oid)
        if sess.destroy_if_exists():
            print(build_ctlseq('KILLed:', oid, output_str=True),
                  flush=True)
    
    sess = Session(args.term)
    sess.create_if_not_exists()
    
    if 'SSH_AUTH_SOCK' in os.environ:
        try:
            os.unlink(sess.RoSSH_SOCK_PATH)
        except FileNotFoundError:
            pass
        os.symlink(os.environ['SSH_AUTH_SOCK'], sess.RoSSH_SOCK_PATH)

    sess.attach()
