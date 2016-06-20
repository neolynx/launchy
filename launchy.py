#!/usr/bin/python
#
# Launchy - or how to lauch subprocesses in python
#
# ./launchy.py dmesg
# ./launchy.py unbuffer dmesg        # with colors 
# ./launchy.py unbuffer dmesg -w     # not terminating
# ./launchy.py apt-get update -y     # no stdin yet
# ./launchy.py ls --color /bin /bum  # with stderr
# ./launchy.py killall rain          # ;-(
#
# Andre Roth <neolynx@gmail.com>
# Sun, 19 Jun 2016 14:05:20 +0200
#

from signal import signal, SIGCHLD
from threading import Thread
from subprocess import Popen
from select import select
from os import waitpid, fdopen, pipe, setsid, O_NONBLOCK
from fcntl import fcntl, F_GETFL, F_SETFL

def child_handler(signum, frame):
    pid = waitpid(-1, 0)
    if pid[0] in Launchy._processes:
        Launchy._processes[pid[0]].terminate(pid[1])
    else:
        print "Warning: unknown process died:", pid[0]

signal(SIGCHLD, child_handler)

class Launchy(Thread):
    _processes = {}

    def __init__(self, command, out_handler = None, err_handler = None):
        Thread.__init__(self)

        self.shutdown = False
        self.command = command
        self.return_code = None

        if out_handler:
            self.out_handler = out_handler
        else:
            self.out_handler = self.__out_handler
        if err_handler:
            self.err_handler = err_handler
        else:
            self.err_handler = self.__err_handler

    def __out_handler(self, line):
        print "out:", line
    
    def __err_handler(self, line):
        print "err:", line

    @classmethod
    def __popen_no_signals(self):
        setsid()

    @classmethod
    def __mkpipe(self):
        r, w = pipe() 
        fl = fcntl(r, F_GETFL)
        fcntl(r, F_SETFL, fl | O_NONBLOCK)
        return (r, w)

    def launch(self):
        print "launch", self, self.command
        self.up = True
        self.proc = None

        self.r_out, self.w_out = self.__mkpipe()
        self.r_err, self.w_err = self.__mkpipe()


        try:
            self.proc = Popen( 
                self.command.strip().split(' '), 
                bufsize = 0, 
                stdin = None, 
                stdout = self.w_out, 
                stderr = self.w_err,
                close_fds = True, 
                preexec_fn = Launchy.__popen_no_signals
            )
            Launchy._processes[self.proc.pid] = self
            print "start", self, self.command
            self.start()
        except OSError, e:
            print "%s: %s"%(str(e), self.command.strip())


    def run(self):
        print "run", self, self.command, self.r_out, self.r_err
        remainder = { self.r_out: "", self.r_err: "" }
        handler = { self.r_out: self.out_handler, self.r_err: self.err_handler }
        stream_out = fdopen(self.r_out)
        stream_err = fdopen(self.r_err)
        while self.up:
            readables, _, _ = select([stream_out, stream_err], [] , [], 0.2)
            
            for readable in readables:
                data = readable.read()
                if remainder[readable.fileno()]:
                    data = remainder[readable.fileno()] + data
                    remainder[readable.fileno()] = ""
                data = data.replace('\r', '\n')
                lines = data.split('\n')
                if lines[-1] == '':
                    lines.pop()
                else:
                    remainder[readable.fileno()] = lines.pop()

                h = handler[readable.fileno()]
                for line in lines:
                    h(line)
            
            if self.shutdown and len(readables) == 0:
                break
        print "end", self

    def terminate(self, return_code):
        print "term", self, self.command
        self.shutdown = True
        self.return_code = return_code >> 8
        pass

    def stop(self):
        if self.proc:
            self.proc.send_signal(SIGTERM)

    def wait(self):
        # never use join() without timeout as it blocks signals,
        # always use this construct:
        while self.isAlive():
            self.join(10)
        return self.return_code


#################################################################
# Usage:
#

from sys import argv
from signal import signal, SIGINT, SIGTERM, SIGQUIT

def shutdown_handler(signum, frame):
    print "\nTerminating..."
    l.stop()

signal(SIGINT,  shutdown_handler)
signal(SIGTERM, shutdown_handler)
signal(SIGQUIT, shutdown_handler)

def my_out(line):
    print "OUT:", line

def my_err(line):
    print "ERR:", line

if __name__ == "__main__":
    if len(argv) > 1:
        command = " ".join(argv[1:])
        print "Launching command: %s"%command
        l = Launchy( command, my_out, my_err )
        l.launch()
        ret = l.wait()
        print "Command returned:", ret
    else:
        l = Launchy( "unbuffer dmesg -w" )
        l.launch()
        l2 = Launchy( "ls -l --color / /gold" )
        l2.launch()
        # cannot use time.sleep, as it is interrupted by the SIGCHLD
        print "press enter to stop"
        raw_input()
        print "ok"
        l.stop()
        l.wait()
        l2.wait()


