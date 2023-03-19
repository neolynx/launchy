#!/usr/bin/env python3

__all__ = ['Launchy']

# define package metadata
__VERSION__ = '0.2.0'

import asyncio
from os import setsid
import shlex
from sys import argv
import signal
import functools
from time import sleep


async def queue_reader(queue, handler):
    while True:
        line = await queue.get()
        if line is None:
            break
        await handler(line)


class Launchy:
    _processes = []

    def __init__(self, command, out_handler=None, err_handler=None, on_exit=None, buffered=True, collect_time=0,
                 **subprocessargs):
        if isinstance(command, list):
            self.args = command
            self.command = " ".join(command)
        else:
            self.command = command
            self.args = shlex.split(command)

        self.buffered = buffered
        self.collect_time = collect_time
        self.subprocessargs = subprocessargs
        self.return_code = None
        self.transport = None
        self.started = asyncio.Future()
        self.cmd_done = asyncio.Future()
        self.terminated = asyncio.Future()
        self.reading_done = asyncio.Future()
        self.stdout_queue = asyncio.Queue()
        self.stderr_queue = asyncio.Queue()

        if out_handler:
            self.out_handler = out_handler
        else:
            self.out_handler = self.__out_handler
        if err_handler:
            self.err_handler = err_handler
        else:
            self.err_handler = self.__err_handler
        if on_exit:
            self.on_exit = on_exit
        else:
            self.on_exit = self.__on_exit

    async def __out_handler(self, data):
        print("out:", data)

    async def __err_handler(self, data):
        print("err:", data)

    async def __on_exit(self, ret):
        pass
        # self.err_handler("terminated with %d"%ret)

    @classmethod
    def __popen_no_signals(self):
        setsid()

    @staticmethod
    def attach_loop(loop):
        watcher = asyncio.SafeChildWatcher()
        # FIXME: remove
        watcher.attach_loop(loop)
        asyncio.set_child_watcher(watcher)

    async def launch(self):
        Launchy._processes.append(self)
        loop = asyncio.get_event_loop()

        class IOProtocol(asyncio.SubprocessProtocol):
            def __init__(self, launchy):
                super().__init__()
                self.launchy = launchy
                self.remainder = {}

            def pipe_data_received(self, fd, data):
                data = data.decode('utf-8', errors='ignore')
                if self.launchy.buffered:
                    if fd not in self.remainder:
                        self.remainder[fd] = ""
                    if self.remainder[fd]:
                        data = self.remainder[fd] + data
                        self.remainder[fd] = ""

                    lines = data.split('\n')
                    if lines[-1] == '':
                        lines.pop()
                    else:
                        self.remainder[fd] = lines.pop()

                    for line in lines:
                        if fd == 1:
                            loop.call_soon(self.launchy.stdout_queue.put_nowait, line)
                        else:
                            loop.call_soon(self.launchy.stderr_queue.put_nowait, line)

                else:  # unbuffered
                    if fd == 1:
                        loop.call_soon(self.launchy.stdout_queue.put_nowait, data)
                    else:
                        loop.call_soon(self.launchy.stderr_queue.put_nowait, data)
                    if self.launchy.collect_time:
                        sleep(self.launchy.collect_time)

            def process_exited(self):
                self.launchy.cmd_done.set_result(True)

            def connection_lost(self, exc):
                self.launchy.reading_done.set_result(True)

        async def bkg(self):
            out_task = loop.create_task(queue_reader(self.stdout_queue, self.out_handler))
            err_task = loop.create_task(queue_reader(self.stderr_queue, self.err_handler))

            try:
                self.transport, protocol = await self.create
            except Exception as exc:
                loop.call_soon(self.stderr_queue.put_nowait, "Error launching process: %s" % self.command)
                loop.call_soon(self.stderr_queue.put_nowait, str(exc))
                Launchy._processes.remove(self)
                self.started.set_result(False)
                self.terminated.set_result(-1)
                return

            self.started.set_result(True)
            await self.reading_done
            await self.cmd_done
            await self.stdout_queue.put(None)
            await self.stderr_queue.put(None)
            await out_task
            await err_task

            Launchy._processes.remove(self)
            return_code = self.transport.get_returncode()
            self.transport.close()
            if self.on_exit:
                await self.on_exit(return_code)
            self.terminated.set_result(return_code)

        self.create = loop.subprocess_exec(
            lambda: IOProtocol(self),
            *self.args,
            stdin=None,
            close_fds=True,
            preexec_fn=Launchy.__popen_no_signals,
            **self.subprocessargs
        )

        loop.create_task(bkg(self))
        await self.started

    async def wait(self):
        ret = await self.terminated
        return ret

    def terminate(self):
        if self.transport:
            self.transport.terminate()
        else:
            loop = asyncio.get_event_loop()
            loop.call_soon(self.stderr_queue.put_nowait, "terminate: no transport")

    def kill(self):
        if self.transport:
            self.transport.kill()
        else:
            loop = asyncio.get_event_loop()
            loop.call_soon(self.stderr_queue.put_nowait, "kill: no transport")

    @classmethod
    async def stop(self):
        for p in Launchy._processes:
            p.terminate()
        for i in range(5):
            if len(Launchy._processes) == 0:
                break
            await asyncio.sleep(1)
        for p in Launchy._processes:
            p.kill()
        for i in range(5):
            if len(Launchy._processes) == 0:
                break
            await asyncio.sleep(1)


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    Launchy.attach_loop(loop)

    launchy = Launchy(argv[1:])

    def sighandler(signame):
        print("terminating (%s received)" % signame)
        launchy.terminate()

    for signame in ('SIGINT', 'SIGTERM'):
        loop.add_signal_handler(getattr(signal, signame), functools.partial(sighandler, signame))

    async def main():
        await launchy.launch()
        await launchy.wait()
        await Launchy.stop()

    try:
        loop.run_until_complete(main())
    except Exception as exc:
        print(exc)

    loop.close()
