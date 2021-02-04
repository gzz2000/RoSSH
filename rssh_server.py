#!/usr/bin/env python3

'''
@file rssh_server.py
@author Zizheng Guo (https://github.com/gzz2000)
@brief The code that would run on the remote server. It creates and manages shells behind pseudo terminals.
'''

import pty
import tty
import os
import sys
import argparse
import signal
import select
import fcntl
import termios
import contextlib
import shutil

SHELL = os.environ.get('SHELL', 'sh')

parser = argparse.ArgumentParser(
    description='RSSH server script that creates and manages shells behind pseudo terminals.')
parser.add_argument('-t', dest='term', type=str, required=True,
                    help='Terminal id to create or attach to')

def build_ctlseq(*args):
    ret = b'\x1b+'
    for a in args:
        ret += bytes(a)
    return ret

def write_to(fd, data):
    while data:
        n = os.write(fd, data)
        data = data[n:]

def write_to_master_fd(master_fd, data):
    '''
    Write all data to master_fd
    Interpret our special control characters, currently only:
    \x1b + WS <8 bytes struct winsize>: change window size
    '''
    if len(data) >= 12 and data[0:4] == b'\x1b+WS':
        ws = data[4:12]
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, ws)
        data = data[12:]
    while data:
        n = os.write(master_fd, data)
        data = data[n:]

@contextlib.contextmanager
def raw_tty():
    STDIN_FILENO = sys.stdin.fileno()
    try:
        mode = tty.tcgetattr(STDIN_FILENO)
        tty.setraw(STDIN_FILENO)
        restore = 1
    except tty.error:
        restore = 0

    try:
        yield
    finally:
        if restore:
            tty.tcsetattr(STDIN_FILENO, tty.TCSAFLUSH, mode)

class Session:
    def __init__(self, term_id):
        self.RSSH_DIRNAME = 'rssh.%s' % term_id
        self.RSSH_DIR = '/tmp/%s' % self.RSSH_DIRNAME
        self.RSSH_PID_PATH = '%s/pid' % self.RSSH_DIR
        self.RSSH_SOCK_PATH = '%s/auth.sock' % self.RSSH_DIR
        self.RSSH_INPUT_PIPE_PATH = '%s/input' % self.RSSH_DIR
        self.RSSH_OUTPUT_PIPE_PATH = '%s/output' % self.RSSH_DIR

    def copy_to_daemon(self, master_fd):
        f_input = open(self.RSSH_INPUT_PIPE_PATH, 'r')
        f_input_fileno = f_input.fileno()
        f_output = open(self.RSSH_OUTPUT_PIPE_PATH, 'w')
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
                        f_input.close
                        f_input = open(self.RSSH_INPUT_PIPE_PATH, 'r')
                        f_input_fileno = f_input.fileno()
                        fds.append(f_input_fileno)
                    else:
                        write_to_master_fd(master_fd, data)
        finally:
            f_output.close()
            f_input.close()

    def create_session_daemon(self):
        os.mkfifo(self.RSSH_INPUT_PIPE_PATH)
        os.mkfifo(self.RSSH_OUTPUT_PIPE_PATH)
        
        child_pid = os.fork()
        if child_pid != 0:
            return
        
        # below runs in the session daemon, and exits.
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        if 'SSH_AUTH_SOCK' in os.environ:
            os.environ['SSH_AUTH_SOCK'] = self.RSSH_SOCK_PATH

        # create the shell
        # FROM pty.spawn source code at https://github.com/python/cpython/blob/3.9/Lib/pty.py#L151
        sys.stdout.write('[RSSH session] created a new shell.\n')

        shell_pid, master_fd = pty.fork()
        if shell_pid == 0:
            print(SHELL)
            os.execvp(SHELL, [SHELL])

        try:
            self.copy_to_daemon(master_fd)
        except OSError:
            pass

        os.close(master_fd)
        retv = os.waitpid(shell_pid, 0)[1]

        sys.stdout.write('[RSSH session] shell exited with status %d.\n' % retv)
        sys.exit(0)

    def create_if_not_exists(self):
        if os.path.exists(self.RSSH_DIR):
            return
        os.mkdir(self.RSSH_DIR, 0o700)
        self.create_session_daemon()

    def attach(self):
        rssh_input = open(self.RSSH_INPUT_PIPE_PATH, 'w')
        rssh_input_fileno = rssh_input.fileno()
        rssh_output = open(self.RSSH_OUTPUT_PIPE_PATH, 'r')
        rssh_output_fileno = rssh_output.fileno()
        stdin_fileno = sys.stdin.fileno()
        stdout_fileno = sys.stdout.fileno()

        def sighandler_winch(signum, frame):
            window_size = fcntl.ioctl(stdin_fileno, termios.TIOCGWINSZ, '00000000')
            os.write(rssh_input_fileno, build_ctlseq(b'WS', window_size))

        signal.signal(signal.SIGWINCH, sighandler_winch)
        
        sys.stdout.write("[RSSH conn] connected to session\n")

        with raw_tty():
            fds = [stdin_fileno, rssh_output_fileno]
            
            while True:
                rfds, _, _ = select.select(fds, [], [])
                
                if stdin_fileno in rfds:
                    data = os.read(stdin_fileno, 1024)
                    if not data:
                        sys.stdout.write("[RSSH conn] unexpected input EOF\n")
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
        shutil.rmtree(self.RSSH_DIR)
        
        sys.stdout.write("[RSSH conn] session exited\n")

if __name__ == '__main__':
    args = parser.parse_args()
    sess = Session(args.term)

    sess.create_if_not_exists()
    
    if 'SSH_AUTH_SOCK' in os.environ:
        try:
            os.unlink(sess.RSSH_SOCK_PATH)
        except FileNotFoundError:
            pass
        os.symlink(os.environ['SSH_AUTH_SOCK'], sess.RSSH_SOCK_PATH)

    sess.attach()
