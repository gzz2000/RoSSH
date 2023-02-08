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
import base64

from rossh_common import \
    rossh_version_index, \
    gen_term_id, \
    build_ctlseq, \
    find_ctlseq_param, \
    write_to, \
    write_to_master_fd, \
    forward_window_resize, \
    raw_tty, \
    lock_fd, \
    PatternFinder

banner = '''\
   ___       __________ __
  / _ \___  / __/ __/ // /
 / , _/ _ \_\ \_\ \/ _  /
/_/|_|\___/___/___/_//_/

Robost SSH (RoSSH) version ''' + str(rossh_version_index) + '''.
Copyright (C) 2023 Zizheng Guo, released under GPL license.
'''

curdir = os.path.dirname(os.path.abspath(__file__))
stdin_fileno = sys.stdin.fileno()
stdout_fileno = sys.stdout.fileno()

# set this to print robust client-server interactions.
is_debug = False

# set this to automatically try reconnecting when disconnected.
# in unstable networks, this may generate a lot of logs.
is_auto_reconnect = False

class ConnectionError(Exception):
    pass

class ConnectionFatalError(Exception):
    pass

def arg_orphaned_sessions():
    ret = []
    for fname in os.listdir(curdir):
        if fname[:8] == '.orphan.':
            locked = False
            try:
                with open(os.path.join(curdir, fname)) as f:
                    lock_fd(f)
            except OSError:
                # print('file %s locked' % fname)
                locked = True
            if not locked: ret.append(fname[8:])
    if ret:
        return ' --kill ' + ' '.join(ret)
    else:
        return ''

class ClientSession:
    def __init__(self, term_id, args):
        self.term_id = term_id
        self.args = args

    def init_connection(self, master_fd):
        pattern_finder = PatternFinder()
        with raw_tty():
            fds = [master_fd, stdin_fileno]

            # loop until ssh established (with a shell for us).
            while True:
                rfds, _, _ = select.select(fds, [], [])

                if master_fd in rfds:
                    data = os.read(master_fd, 1024)
                    if is_debug: write_to(stdout_fileno, b'{' + data + b'}')
                    if not data:
                        raise ConnectionError('Master fd closed')

                    # before connection establishes, connect the SSH
                    # terminal to stdin/stdout, for entering password, etc.

                    found_prompt = pattern_finder.find_with_tail(
                        data, build_ctlseq('PROMPT'))
                    if found_prompt:
                        write_to(stdout_fileno, found_prompt[0])
                        break

                    pattern_finder.append(data)
                    # write out server messages (like request for password
                    # input)
                    write_to(stdout_fileno, data)

                # before ssh establishment, we allow stdin interaction
                # for user authentication (e.g., password input).
                if stdin_fileno in rfds:
                    data = os.read(stdin_fileno, 1024)
                    write_to(master_fd, data)

            # after ssh establishment, we set up the remote server.
            for is_second_try in [False, True]:
                term_environ = '' if 'TERM' not in os.environ else \
                    os.environ['TERM']
                write_to(
                    master_fd,
                    b'(unset PS1 PS2 PS3 && env TERM=\'' +
                    bytes(term_environ, encoding='utf-8') + \
                    b'\' ' +
                    bytes('~/.rossh/rossh_server.py -V %d -t %s %s' %
                          (rossh_version_index, self.term_id,
                           arg_orphaned_sessions()),
                          encoding='utf-8') +
                    b' && exit)\n')

                # loop for server response.
                # either the connection is established and our server
                # returns a special message => we exit the function,
                # or, something wrong happens, and then we try to solve it.
                is_server_old_version = False
                while True:
                    data = os.read(master_fd, 1024)
                    if is_debug: write_to(stdout_fileno, b'{' + data + b'}')
                    if not data:
                        raise ConnectionError('Master fd closed after ssh')

                    # orphan kill information
                    # not robust with patternfinder here, because im lazy..
                    killed_term_id, remain_data = find_ctlseq_param(
                        data, 'KILLed:', output_str=True)
                    if killed_term_id is not None:
                        print('[RoSSH] killed orphaned session %s\r'
                              % killed_term_id)
                        try:
                            os.unlink(os.path.join(
                                curdir, '.orphan.%s' % killed_term_id))
                        except Exception as e:
                            print(e, '\r')
                        data = remain_data

                    # connection success, exit
                    found_success = pattern_finder.find_with_tail(
                        data, build_ctlseq('CONN:S')
                    )
                    if found_success:
                        write_to(stdout_fileno, found_success[1])
                        return

                    # server has an old version
                    # the version error must respect previous versions.
                    if pattern_finder.find_with_tail(data, b'\x1b+CONN:FL:VER') or \
                       pattern_finder.find_with_tail(
                           data, build_ctlseq('CONN:FL:VER:SERVER_UPDATE')):
                        is_server_old_version = True

                    if pattern_finder.find_with_tail(
                           data, build_ctlseq('CONN:FL:VER:CLIENT_TOOOLD')):
                        raise ConnectionFatalError(
                            'This client version (' +
                            str(rossh_version_index) +
                            ') is older than the server-installed '
                            'RoSSH version. Please upgrade your client.')

                    if pattern_finder.find_with_tail(
                            data, b'/usr/bin/env:') and \
                       pattern_finder.find_with_tail(
                            data, b'No such file or directory'):
                        print('[RoSSH] No Python 3 found at remote server. '
                              'You must install one to use RoSSH.\r')
                        raise ConnectionFatalError('No python 3 found.')

                    # by any consequence, we are bounced back to the
                    # shell prompt. we try to install / upgrade the server.
                    if pattern_finder.find_with_tail(
                            data, build_ctlseq('PROMPT')):
                        pattern_finder.append(data)
                        
                        if is_second_try:
                            raise ConnectionError(
                                'Failed even after installation. '
                                'Please report this to GitHub issues: '
                                'https://github.com/gzz2000/RoSSH/issues')
                        
                        if is_server_old_version:
                            print('[RoSSH] Upgrading RoSSH '
                                  'at remote server ...\r')
                        else:
                            print('[RoSSH] Copying RoSSH '
                                  'to remote server ~/.rossh ...\r')

                        # run a command and wait for success response.
                        # this seems to be more robust.
                        def run_cmd(cmd):
                            assert b'\n' not in cmd
                            write_to(master_fd, cmd + b'\n')
                            while True:
                                data = os.read(master_fd, 4096)
                                if is_debug: write_to(stdout_fileno, b'{' + data + b'}')
                                if not data:
                                    raise RuntimeError(
                                        'Unexpected EOF running command')
                                if pattern_finder.find_with_tail(
                                        data, build_ctlseq('PROMPT')):
                                    pattern_finder.append(data)
                                    break
                                pattern_finder.append(data)

                        # instructions = bytes()
                        run_cmd(b'mkdir -p ~/.rossh')
                        run_cmd(b'chmod go-w ~/.rossh')
                        run_cmd(b'cd ~/.rossh')

                        for fname in ['rossh_client.py',
                                      'rossh_server.py',
                                      'rossh_common.py']:
                            run_cmd(b'rm ' + bytes(fname, encoding='utf-8'))
                            with open(os.path.join(curdir, fname), 'rb') as f:
                                while True:
                                    data = f.read(1023)
                                    if not data:
                                        break
                                    run_cmd(b'echo "' +
                                            base64.b64encode(data) +
                                            b'" | base64 -d >> ' +
                                            bytes(fname, encoding='utf-8'))

                        run_cmd(b'chmod go-w,+x *')
                        run_cmd(b'cd ~')
                        # now, we fall back to the second try (outer loop)...
                        break

                    # continue looking for PROMPTs / success..
                    pattern_finder.append(data)

        assert False, 'unreachable'

    def connect(self):
        args = self.args + \
               ['-t', 'exec', 'env',
                'TERM=\'dumb\'',
                'PS1=\'' + build_ctlseq('PROMPT', output_str=True) + '\'',
                'PS2=\'\'',
                'PS3=\'\'',
                '/bin/sh']
        print('[RoSSH] Connecting to: ' + ' '.join(self.args) +
              ' -t <rossh_server>')
        if is_debug: print('debug', args)
        ssh_pid, master_fd = pty.fork()
        if ssh_pid == 0:
            os.execvp(args[0], args)

        # init connection.
        success = False
        fatal = False
        try:
            self.init_connection(master_fd)
            success = True
        except (OSError, ConnectionError) as e:
            print('[RoSSH] Connection failed: %s' % e)
        except ConnectionFatalError as e:
            fatal = True
            raise e
        except Exception as e:
            fatal = True
            print('\r\n[RoSSH] unexpected error connect:', e)
            raise e
        # except ConnectionFatalError as e (outside the call to connect)
        finally:
            # cleanup
            if not success:
                try:
                    os.close(master_fd)
                except OSError:
                    pass
                try:
                    os.kill(ssh_pid, signal.SIGINT)
                    os.waitpid(ssh_pid, 0)
                except ProcessLookupError:
                    pass
                if not fatal:
                    return None, None
        return ssh_pid, master_fd

    def attach(self):
        session_created = False
        skip_reconnect = False
        
        while True:
            if skip_reconnect:
                skip_reconnect = False
            else:
                try:
                    ssh_pid, master_fd = self.connect()
                except ConnectionFatalError as e:
                    print('[RoSSH] Connection failed with fatal error: %s'
                          % e)
                    return
                except Exception as e:
                    print('\r\n[RoSSH] unexpected fatal error on attach:',
                          e)
                    return
            if master_fd is None:
                print('[RoSSH] Press any key to retry. '
                      'Ctrl-C to give up.')
                with raw_tty():
                    data = os.read(stdin_fileno, 1024)
                if data == b'\x03':
                    if session_created:
                        print('[RoSSH] You will not be able '
                              'to reconnect to this session. '
                              'Proceed? (y/n)')
                        with raw_tty():
                            while True:
                                data = os.read(stdin_fileno, 1024)
                                if data in [b'y', b'n', b'Y', b'N']:
                                    break
                        if data in [b'y', b'Y']:
                            print('[RoSSH] The orphaned remote '
                                  'session would be killed the '
                                  'next time you log into this '
                                  'server from this client.')
                            return
                    else:
                        return
                continue

            if not session_created:
                session_created = True
                f_session_orphan = open(
                    os.path.join(
                        curdir,
                        '.orphan.%s' % self.term_id), 'w')
                lock_fd(f_session_orphan)

            try:
                pattern_finder = PatternFinder()
                with raw_tty(), forward_window_resize(master_fd, indirect=False):
                    fds = [master_fd, stdin_fileno]

                    while True:
                        rfds, _, _ = select.select(fds, [], [])

                        if stdin_fileno in rfds:
                            data = os.read(stdin_fileno, 1024)
                            write_to(master_fd, data)

                        if master_fd in rfds:
                            data = os.read(master_fd, 1024)
                            if is_debug: write_to(stdout_fileno, data)
                            if not data:
                                print('\r\n[RoSSH] SSH disconnected.\r')
                                # no longer reconnect automatically
                                skip_reconnect = not is_auto_reconnect
                                break

                            # before connection establishes,
                            # connect the SSH terminal
                            # to stdin/stdout,
                            # for entering password, etc.

                            found_end = pattern_finder.find_with_tail(
                                data, build_ctlseq('CONN:E'))
                            if found_end:
                                write_to(stdout_fileno, found_end[0])
                                f_session_orphan.close()
                                os.unlink(os.path.join(
                                    curdir, '.orphan.%s' % self.term_id))
                                if is_debug:
                                    print('\r[RoSSH] Exited gracefully.\r')
                                return

                            pattern_finder.append(data)
                            write_to(stdout_fileno, data)
                            
            except IOError as e:
                print('\r\n[RoSSH] SSH disconnected.\r')
                skip_reconnect = not is_auto_reconnect
                continue
            
            finally:
                try:
                    os.close(master_fd)
                except OSError:
                    pass
                try:
                    os.kill(ssh_pid, signal.SIGINT)
                    os.waitpid(ssh_pid, 0)
                except ProcessLookupError:
                    pass
                ssh_pid, master_fd = None, None

if __name__ == '__main__':
    print(banner)
    term_id = gen_term_id()
    args = [*sys.argv]
    args[0] = 'ssh'
    sess = ClientSession(term_id, args)
    sess.attach()
