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

rossh_version_index = 4

ctlseq_special = 'rossh_173e6793-122c'
ctlseq_begin = b'BC'
ctlseq_end = b'ECrossh'

def gen_term_id():
    chars = string.ascii_uppercase + string.ascii_lowercase + string.digits
    return ''.join(random.choice(chars) for _ in range(16))

def to_bytes(s):
    if isinstance(s, str):
        return bytes(s, encoding='utf-8')
    else:
        return bytes(s)

def find_ctlseq_param(data, *args, output_str=False):
    begin = ctlseq_begin + to_bytes(ctlseq_special)
    for a in args:
        begin += to_bytes(a)
    st = data.find(begin)
    if st < 0:
        return None, None
    data = data[st + len(begin):]
    ed = data.find(ctlseq_end)
    if ed < 0:
        raise RuntimeError('incomplete ctlseq param for %r' % args)
    ret = data[:ed]
    if output_str:
        ret = ret.decode('utf-8')
    return ret, data[ed + len(ctlseq_end):]

def build_ctlseq(*args, output_str=False):
    ret = ctlseq_begin + to_bytes(ctlseq_special)
    for a in args:
        ret += to_bytes(a)
    ret += ctlseq_end
    if output_str:
        ret = ret.decode('utf-8')
    return ret

def write_to(fd, data):
    while data:
        n = os.write(fd, data)
        data = data[n:]

def write_to_master_fd(master_fd, data):
    '''
    Write all data to master_fd
    Interpret our special control characters, currently only:
    WS + <8 bytes struct winsize>: change window size
    '''
    ws, remain_data = find_ctlseq_param(data, 'WS')
    if ws is not None:
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, ws)
        data = remain_data
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
    If indirect is True, we send WS command to input_fileno.
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

# used to detect patterns that are split into two consecutive packets.
class PatternFinder:
    def __init__(self):
        self.tail_buf = bytes()
        self.BUFLEN = 100  # should be longer than the largest pattern

    def append(self, data):
        self.tail_buf = (self.tail_buf + data)[-self.BUFLEN:]

    def find_with_tail(self, data, pattern):
        assert len(pattern) >= 2
        assert len(pattern) < self.BUFLEN
        tail_head_len = min(len(pattern) - 1, len(self.tail_buf))
        p = (self.tail_buf[-(len(pattern) - 1):] + data).find(pattern)
        if p < 0:
            return None
        before = data[:p - tail_head_len] if p >= tail_head_len else b''
        after = data[p + len(pattern) - tail_head_len:]
        return (before, after)
