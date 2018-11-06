#!/usr/bin/env python3
import threading
import re
import signal
import os
import curses
import keyword
import sys
import time
import ast
import astunparse
import collections
import atexit

def annotate(body):
    offset = 0
    for i in range(len(body)):
        idx = i + offset
        node = body[idx]
        if isinstance(node, (ast.Expr, ast.Assign, ast.Delete,
                             ast.AugAssign, ast.Import, ast.ImportFrom)):
            pre = ast.parse(f'_lineprof.line_pre({node.lineno})').body[0]
            post = ast.parse(f'_lineprof.line_post({node.lineno})').body[0]
            body.insert(idx, pre)
            body.insert(idx+2, post)
            offset += 2
        elif isinstance(node, (ast.Return, ast.Break, ast.Continue)):
            pre = ast.parse(f'_lineprof.exit_pre({node.lineno})').body[0]
            body.insert(idx, pre)
            offset += 1
            pass
        elif isinstance(node, (ast.If, ast.While, ast.For)):
            annotate(node.body)
            annotate(node.orelse)
        elif isinstance(node, ast.Try):
            annotate(node.body)
            annotate(node.orelse)
            for handler in node.handlers:
                annotate(handler.body)
            annotate(node.finalbody)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.With)):
            annotate(node.body)
        else:
            print('WARNING', type(node))

class LineProf:
    def __init__(self, reporter):
        self.begin_time = {}
        self.clear()
        self.reporter = reporter

    def clear(self):
        self.line_time = collections.defaultdict(float)
        self.line_evals = collections.defaultdict(int)
        self.last_report = time.monotonic()

    def line_pre(self, lineno):
        self.begin_time[lineno] = time.monotonic()

    def exit_pre(self, lineno):
        self.line_evals[lineno] += 1

    def line_post(self, lineno):
        now = time.monotonic()
        self.line_time[lineno] += now - self.begin_time[lineno]
        self.line_evals[lineno] += 1
        interval = now - self.last_report
        if interval > 1:
            self.report(interval)

    def report(self, interval=None):
        if interval is None:
            interval = time.monotonic() - self.last_report
        self.reporter.report(interval, self.line_time, self.line_evals)
        self.clear()

class Reporter:
    def __init__(self, code, stdscr):
        self.tree = ast.parse(code)
        annotate(self.tree.body)
        self.lines = code.splitlines()
        self.keywords = []
        kwpattern = '\\b(' + '|'.join(map(re.escape, keyword.kwlist)) + ')\\b'
        for line in self.lines:
            keywords = []
            for m in re.finditer(kwpattern, line):
                keywords.append((m.start(0), m.group(0)))
            self.keywords.append(keywords)
        self.stdscr = stdscr
        self.data = 1, collections.defaultdict(float), collections.defaultdict(float)
        self.dirty = False
        self.scroll = 0
        self.height = 10

    def run(self):
        self.input_thread_isnt = threading.Thread(target=self.input_thread)
        self.input_thread_isnt.setDaemon(True)
        self.input_thread_isnt.start()
        self.draw_thread_isnt = threading.Thread(target=self.draw_thread)
        self.draw_thread_isnt.setDaemon(True)
        self.draw_thread_isnt.start()
        self.lineprof = LineProf(self)
        exec(compile(self.tree, 'fname', 'exec'), dict(_lineprof=self.lineprof))

    def report(self, interval, total_time, evals):
        self.data = interval, total_time, evals
        self.invalidate()

    def invalidate(self):
        self.dirty = True

    def input_thread(self):
        while True:
            c = self.stdscr.getch()
            if c == ord('j'): self.scroll += 1
            if c == ord('k'): self.scroll -= 1
            if c == ord('d'): self.scroll += self.height//2
            if c == ord('u'): self.scroll -= self.height//2
            if c == ord(' '): self.scroll += self.height
            if c == ord('q'): os.kill(os.getpid(), signal.SIGINT)
            if self.scroll < 0: self.scroll = 0
            if self.scroll > len(self.lines) - self.height + 2:
                self.scroll = len(self.lines) - self.height + 2
            self.invalidate()

    def draw_thread(self):
        while True:
            if not self.dirty:
                time.sleep(0.1)
            height, width = self.stdscr.getmaxyx()
            self.height = height
            interval, total_time, evals = self.data
            self.stdscr.clear()
            for lineno, line in enumerate(self.lines, 1):
                screen_line = lineno - self.scroll
                if not 0 < screen_line < height:
                    continue
                part = total_time[lineno]/interval*100
                if part < 0.1:
                    percent = ''
                else:
                    percent = f'{part:4.1f}%'
                fr = evals[lineno]/interval
                if fr == 0:
                    freq = ''
                elif fr < 1000:
                    freq =  f'{fr:5.1f}Hz'
                elif fr < 1000000:
                    freq =  f'{fr/1000:5.1f}k'
                else:
                    freq =  f'{fr/1000000:5.1f}M'
                self.stdscr.addstr(screen_line, 1, percent, curses.color_pair(5))
                self.stdscr.addstr(screen_line, 7, freq, curses.color_pair(5))
                if part > 20:
                    self.stdscr.addstr(screen_line, 15, line, curses.color_pair(2))
                elif part > 5:
                    self.stdscr.addstr(screen_line, 15, line, curses.color_pair(4))
                else:
                    self.stdscr.addstr(screen_line, 15, line)
                for idx, keyword in self.keywords[lineno-1]:
                    self.stdscr.addstr(screen_line, 15+idx, keyword, curses.color_pair(1))
            self.stdscr.refresh()
            self.dirty = False

    def dump(self):
        print(astunparse.unparse(self.tree))

code = '''
'''

if len(sys.argv) > 1:
    code = open(sys.argv[1]).read()

def main(stdscr):
    curses.start_color()
    curses.use_default_colors()
    for i in range(0, curses.COLORS):
        curses.init_pair(i + 1, i, -1)
    stdout = sys.stdout
    stderr = sys.stderr
    @atexit.register
    def on_exit():
        sys.stdout = stdout
        sys.stderr = stderr
        curses.KEY_SCOMMAND
        curses.endwin()
    def signal_handler(a, b):
        on_exit()
        exit()
    signal.signal(signal.SIGINT, signal_handler)
    # sys.stdout = open(os.devnull, 'w')
    # sys.stderr = open(os.devnull, 'w')
    reporter = Reporter(code, stdscr)
    try:
        reporter.run()
    except KeyboardInterrupt:
        pass
    sys.stdout = stdout
    sys.stderr = stderr

curses.wrapper(main)
