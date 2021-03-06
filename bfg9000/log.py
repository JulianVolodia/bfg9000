import colorama
import logging
import os
import sys
import traceback
import warnings

getLogger = logging.getLogger


def _is_bfg_src(filename):
    rel = os.path.relpath(filename, os.path.dirname(__file__))
    return not rel.startswith(os.pardir + os.sep)


def _filter_stack(stack):
    # Find where the user's stack frames begin and end.
    gen = enumerate(stack)
    for start, line in gen:
        if not _is_bfg_src(line[0]):
            break
    else:
        start = len(stack)

    for end, line in gen:
        if _is_bfg_src(line[0]):
            break
    else:
        end = len(stack)

    return stack[:start], stack[start:end], stack[end:]


def _format_stack(stack):
    return ''.join(traceback.format_list(stack))


class StackFilter(object):
    def __init__(self, has_stack=True):
        self.has_stack = has_stack

    def filter(self, record):
        has_stack = bool((record.exc_info and record.exc_info[0]) or
                         getattr(record, 'full_stack', None))
        return has_stack == self.has_stack


class StackfulStreamHandler(logging.StreamHandler):
    def emit(self, record):
        if record.exc_info:
            record.full_stack = traceback.extract_tb(record.exc_info[2])
            record.exc_info = None

        pre, stack, post = _filter_stack(record.full_stack)
        record.stack_pre = _format_stack(pre)
        record.stack = _format_stack(stack).rstrip()
        record.stack_post = '\n' + _format_stack(post).rstrip()

        record.user_pathname = stack[-1][0]
        record.user_lineno = stack[-1][1]

        return logging.StreamHandler.emit(self, record)


def init(color='auto', debug=False):
    if color == 'always':
        colorama.init(strip=False)
    elif color == 'never':
        colorama.init(strip=True, convert=False)
    else:  # color == 'auto'
        colorama.init()

    logging.addLevelName(logging.CRITICAL,
                         '\033[1;41;37m' + 'critical' + '\033[0m')
    logging.addLevelName(logging.ERROR, '\033[1;31m' + 'error' + '\033[0m')
    logging.addLevelName(logging.WARNING, '\033[1;33m' + 'warning' + '\033[0m')
    logging.addLevelName(logging.INFO, '\033[1;34m' + 'info' + '\033[0m')
    logging.addLevelName(logging.DEBUG, '\033[1;35m' + 'debug' + '\033[0m')

    logging.root.setLevel(logging.DEBUG if debug else logging.WARNING)

    stackless = logging.StreamHandler()
    stackless.addFilter(StackFilter(has_stack=False))
    stackless.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
    logging.root.addHandler(stackless)

    stackful = StackfulStreamHandler()
    stackful.addFilter(StackFilter(has_stack=True))

    fmt = '%(levelname)s: %(user_pathname)s:%(user_lineno)d: %(message)s\n'
    if debug:
        fmt += '\033[2m%(stack_pre)s\033[0m'
    fmt += '%(stack)s'
    if debug:
        fmt += '\033[2m%(stack_post)s\033[0m'

    stackful.setFormatter(logging.Formatter(fmt))
    logging.root.addHandler(stackful)


def _showwarning(message, category, filename, lineno, file=None, line=None):
    stack = traceback.extract_stack()[1:]
    logging.warning(message, extra={'full_stack': stack})


warnings.showwarning = _showwarning
warnings.filterwarnings('once')
