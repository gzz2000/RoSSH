'''
@file rossh_common.py
@author Zizheng Guo (https://github.com/gzz2000)
@brief Utilities used by both rossh_server and rossh_client.
'''

import os
import fcntl
import termios
import contextlib
import tty
import sys
import signal
import random
import string

rossh_version_index = 3

def gen_term_id():
    chars = string.ascii_uppercase + string.ascii_lowercase + string.digits
    return ''.join(random.choice(chars) for _ in range(16))

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
def change_signal(signum, handler):
    old_signal = signal.signal(signum, handler)
    try:
        yield
    finally:
        signal.signal(signum, old_signal)

@contextlib.contextmanager
def forward_window_resize(input_fileno, indirect):
    '''
    Listen for window size change (SIGWINCH) and forward it
    to the terminal at input_fileno.
    If indirect is True, we send \x1b+WS to input_fileno.
    Otherwise, we set window size of input_fileno directly.
    '''
    stdin_fileno = sys.stdin.fileno()
    def resize_window():
        window_size = fcntl.ioctl(stdin_fileno, termios.TIOCGWINSZ, '00000000')
        if indirect:
            os.write(input_fileno, build_ctlseq(b'WS', window_size))
        else:
            fcntl.ioctl(input_fileno, termios.TIOCSWINSZ, window_size)
            
    def new_signal_handler(signum, frame):
        resize_window()

    with change_signal(signal.SIGWINCH, new_signal_handler):
        resize_window()
        yield

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

def lock_fd(fd):
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
