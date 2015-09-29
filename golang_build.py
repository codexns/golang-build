# coding: utf-8
from __future__ import unicode_literals, division, absolute_import, print_function

import sys
import os
import threading
import subprocess
import time
import re
import textwrap
import collections

if sys.version_info < (3,):
    from Queue import Queue
else:
    from queue import Queue

import sublime
import sublime_plugin

import golangconfig
import newterm
import package_events


# A list of the environment variables to pull from settings when creating a
# subprocess. Some subprocesses may have one or more manually overridden.
GO_ENV_VARS = set([
    'GOPATH',
    'GOROOT',
    'GOROOT_FINAL',
    'GOBIN',
    'GOHOSTOS',
    'GOHOSTARCH',
    'GOOS',
    'GOARCH',
    'GOARM',
    'GO386',
    'GORACE',
])

# References to any existing GolangProcess() for a sublime.Window.id(). For
# basic get and set operations, the dict is threadsafe.
_PROCS = {}

# References to any existing GolangPanelPrinter() for a sublime.Window.id().
# If the value is set, there is a printer still processing output. When
# a process is complete, the panel printer will clear itself. For basic get and
# set operations, the dict is threadsafe.
_PRINTERS = {}


class GolangBuildCommand(sublime_plugin.WindowCommand):

    """
    Command to run "go build", "go install", "go test" and "go clean"
    """

    def run(self, task='build'):
        if _yeild_to_running_build(self.window):
            return

        working_dir = _determine_working_dir(self.window)
        if working_dir is None:
            return

        go_bin, env = golangconfig.subprocess_info(
            'go',
            set(['GOPATH']),
            GO_ENV_VARS - set(['GOPATH']),
            view=self.window.active_view(),
            window=self.window,
        )

        if task == 'cross_compile':
            _task_cross_compile(
                self,
                go_bin,
                working_dir,
                env
            )
            return

        proc = _run_process(
            task,
            self.window,
            [go_bin, task, '-v'],
            working_dir,
            env
        )
        _set_proc(self.window, proc)


def _task_cross_compile(command, go_bin, working_dir, env):
    """
    Prompts the user to select the OS and ARCH to use for a cross-compile

    :param command:
        A sublime_plugin.WindowCommand object

    :param go_bin:
        A unicode string with the path to the "go" executable

    :param working_dir:
        A unicode string with the working directory for the "go" executable

    :param env:
        A dict of environment variables to use with the "go" executable
    """

    valid_combinations = [
        ('darwin', '386'),
        ('darwin', 'amd64'),
        ('darwin', 'arm'),
        ('darwin', 'arm64'),
        ('dragonfly', 'amd64'),
        ('freebsd', '386'),
        ('freebsd', 'amd64'),
        ('freebsd', 'arm'),
        ('linux', '386'),
        ('linux', 'amd64'),
        ('linux', 'arm'),
        ('linux', 'arm64'),
        ('linux', 'ppc64'),
        ('linux', 'ppc64le'),
        ('netbsd', '386'),
        ('netbsd', 'amd64'),
        ('netbsd', 'arm'),
        ('openbsd', '386'),
        ('openbsd', 'amd64'),
        ('openbsd', 'arm'),
        ('plan9', '386'),
        ('plan9', 'amd64'),
        ('solaris', 'amd64'),
        ('windows', '386'),
        ('windows', 'amd64'),
    ]

    def on_done(index):
        """
        Processes the user's input and launch the build process

        :param index:
            The index of the option the user selected, or -1 if cancelled
        """

        if index == -1:
            return

        env['GOOS'], env['GOARCH'] = valid_combinations[index]

        proc = _run_process(
            'cross_compile',
            command.window,
            [go_bin, 'build', '-v'],
            working_dir,
            env
        )
        _set_proc(command.window, proc)

    quick_panel_options = []
    for os_, arch in valid_combinations:
        quick_panel_options.append('OS: %s, ARCH: %s' % (os_, arch))

    command.window.show_quick_panel(
        quick_panel_options,
        on_done
    )


class GolangBuildCancelCommand(sublime_plugin.WindowCommand):

    """
    Terminates any existing "go" process that is running for the current window
    """

    def run(self):
        proc = _get_proc(self.window)
        if proc and not proc.finished:
            proc.terminate()
        if proc is not None:
            _set_proc(self.window, None)

    def is_enabled(self):
        proc = _get_proc(self.window)
        if not proc:
            return False
        return not proc.finished


class GolangBuildReopenCommand(sublime_plugin.WindowCommand):

    """
    Reopens the output from the last build command
    """

    def run(self):
        self.window.run_command('show_panel', {'panel': 'output.golang_build'})


class GolangBuildGetCommand(sublime_plugin.WindowCommand):

    """
    Prompts the use to enter the URL of a Go package to get
    """

    def run(self):
        if _yeild_to_running_build(self.window):
            return

        working_dir = _determine_working_dir(self.window)
        if working_dir is None:
            return

        go_bin, env = golangconfig.subprocess_info(
            'go',
            set(['GOPATH']),
            GO_ENV_VARS - set(['GOPATH']),
            view=self.window.active_view(),
            window=self.window,
        )

        def on_done(url):
            """
            Processes the user's input and launches the "go get" command

            :param url:
                A unicode string of the URL to get
            """

            proc = _run_process(
                'get',
                self.window,
                [go_bin, 'get', '-v', url],
                working_dir,
                env
            )
            _set_proc(self.window, proc)

        self.window.show_input_panel(
            'go get',
            '',
            on_done,
            None,
            None
        )


class GolangBuildTerminalCommand(sublime_plugin.WindowCommand):

    """
    Opens a terminal for the user to the directory containing the open file,
    setting any necessary environment variables
    """

    def run(self):

        working_dir = _determine_working_dir(self.window)
        if working_dir is None:
            return

        relevant_sources = set([
            'project file',
            'project file (os-specific)',
            'golang.sublime-settings',
            'golang.sublime-settings (os-specific)'
        ])

        env_overrides = {}
        for var_name in GO_ENV_VARS:
            value, source = golangconfig.setting_value(var_name, window=self.window)
            # Only set overrides that are not coming from the user's shell
            if source in relevant_sources:
                env_overrides[var_name] = value

        newterm.launch_terminal(working_dir, env=env_overrides)


def _yeild_to_running_build(window):
    """
    Check if a build is already running, and if so, allow the user to stop it,
    or cancel the new build

    :param window:
        A sublime.Window of the window the build is being run in

    :return:
        A boolean - if the new build should be abandoned
    """

    proc = _get_proc(window)
    if proc and not proc.finished:
        message = _format_message("""
            Golang Build

            There is already a build running. Would you like to stop it?
        """)
        if not sublime.ok_cancel_dialog(message, 'Stop Running Build'):
            return True
        proc.terminate()
        _set_proc(window, None)

    return False


def _determine_working_dir(window):
    """
    Determine the working directory for a command based on the user's open file
    or open folders

    :param window:
        The sublime.Window object of the window the command was run on

    :return:
        A unicode string of the working directory, or None if no working
        directory was found
    """

    view = window.active_view()
    working_dir = None

    # If a file is open, get the folder from the file, and error if the file
    # has not been saved yet
    if view:
        if view.file_name():
            working_dir = os.path.dirname(view.file_name())

    # If no file is open, then get the list of folders and grab the first one
    else:
        folders = window.folders()
        if len(folders) > 0:
            working_dir = folders[0]

    if working_dir is None or not os.path.exists(working_dir):
        message = _format_message("""
            Golang Build

            No files or folders are open, or the open file or folder does not exist on disk
        """)
        sublime.error_message(message)
        return None

    return working_dir


class GolangProcess():

    """
    A wrapper around subprocess.Popen() that provides information about how
    the process was started and finished, plus a queue.Queue of output
    """

    # A float of the unix timestamp of when the process was started
    started = None

    # A list of strings (unicode for Python 3, byte string for Python 2) of
    # the process path and any arguments passed to it
    args = None

    # A unicode string of the process working directory
    cwd = None

    # A dict of the env passed to the process
    env = None

    # A subprocess.Popen() object of the running process
    proc = None

    # A queue.Queue object of output from the process
    output = None

    # The result of the process, a unicode string of "cancelled", "success" or "error"
    result = None

    # A float of the unix timestamp of when the process ended
    finished = None

    # A threading.Lock() used to prevent the stdout and stderr handlers from
    # both trying to perform process cleanup at the same time
    _cleanup_lock = None

    def __init__(self, args, cwd, env):
        """
        :param args:
            A list of strings (unicode for Python 3, byte string for Python 2)
            of the process path and any arguments passed to it

        :param cwd:
            A unicode string of the working directory for the process

        :param env:
            A dict of strings (unicode for Python 3, byte string for Python 2)
            to pass to the process as the environment variables
        """

        self.args = args
        self.cwd = cwd
        self.env = env

        startupinfo = None
        if sys.platform == 'win32':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        self._cleanup_lock = threading.Lock()
        self.started = time.time()
        self.proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=env,
            startupinfo=startupinfo
        )
        self.finished = False

        self.output = Queue()

        threading.Thread(
            target=self._read_output,
            args=(
                self.output,
                self.proc.stdout.fileno(),
                'stdout'
            )
        ).start()

        threading.Thread(
            target=self._read_output,
            args=(
                self.output,
                self.proc.stderr.fileno(),
                'stderr'
            )
        ).start()

    def wait(self):
        """
        Blocks waiting for the subprocess to complete
        """

        if self.proc:
            self.proc.wait()

    def terminate(self):
        """
        Terminates the subprocess
        """

        if self.proc:
            self._cleanup_lock.acquire()
            try:
                self.proc.terminate()
                self.finished = time.time()
                self.result = 'cancelled'
                self.output.put(('eof', None))
                self.proc = None
            finally:
                self._cleanup_lock.release()

    def _read_output(self, queue, fileno, output_type):
        """
        Handler to process output from stdout/stderr

        RUNS IN A THREAD

        :param queue:
            The queue.Queue object to add the output to

        :param fileno:
            The fileno to read output from

        :param output_type:
            A unicode string of "stdout" or "stderr"
        """

        while self.proc and self.proc.poll() is None:
            chunk = os.read(fileno, 32768)
            if self.proc is None:
                break
            if len(chunk) == 0:
                break
            queue.put((output_type, chunk.decode('utf-8')))
        self._cleanup()

    def _cleanup(self):
        """
        Cleans up the subprocess and marks the state of self appropriately
        """

        self._cleanup_lock.acquire()
        try:
            if not self.proc:
                return
            # Get the returncode to prevent a zombie/defunct child process
            self.proc.wait()
            self.result = 'success' if self.proc.returncode == 0 else 'error'
            self.finished = time.time()
            self.output.put(('eof', None))
            self.proc = None
        finally:
            self._cleanup_lock.release()


class GolangPanelPrinter():

    """
    Displays information about, and the output of, a Go process in a Sublime
    Text output panel
    """

    # The GolangProcess() object the printer is displaying output from
    proc = None

    # The sublime.Window object of the output panel the printer is using
    panel = None

    # Any existing GolangPanelPrinter() object this printer need to wait for
    existing_printer = None

    # A threading.Thread() object that is processing the Queue
    thread = None

    # A boolean if the printer is finished processing output
    finished = None

    def __init__(self, proc, panel, window_id, existing_printer):
        """
        :param proc:
            A GolangProcess() object

        :param panel:
            A sublime.Window object from sublime.Window.get_output_panel()

        :param window_id:
            An integer of the window's id

        :param existing_printer:
            An existing GolangPanelPrinter() that is using the panel
        """

        self.proc = proc
        self.panel = panel
        self.window_id = window_id
        self.existing_printer = existing_printer
        self.finished = False
        self._configure_panel()

        self.thread = threading.Thread(
            target=self._run
        )
        self.thread.start()

    def _configure_panel(self):
        """
        Sets various settings on the output panel
        """

        st_settings = sublime.load_settings('Preferences.sublime-settings')
        panel_settings = self.panel.settings()
        panel_settings.set('syntax', 'Packages/Golang Build/Golang Build Output.tmLanguage')
        panel_settings.set('color_scheme', st_settings.get('color_scheme'))
        panel_settings.set('draw_white_space', 'selection')
        panel_settings.set('word_wrap', False)
        panel_settings.set("auto_indent", False)
        panel_settings.set('line_numbers', False)
        panel_settings.set('gutter', False)
        panel_settings.set('scroll_past_end', False)
        if not self.existing_printer:
            self._set_panel_finished(False, None)

    def _set_panel_finished(self, finished, result):
        """
        Sets a setting on the output view inidicating if the build is finished.
        This primarily exists to allow for testing of the the package.

        :param finished:
            A boolean - if the process has finished

        :param result:
            None or a unicode string of "success", "error" or "cancelled"
        """

        self.panel.settings().set('golang_build_finished', finished)
        self.panel.settings().set('golang_build_result', result)

    def _run(self):
        """
        GolangProcess() output queue processor

        RUNS IN A THREAD
        """

        # If there is currently another printer working with the panel, wait
        # until it completed printing its queue so the output is not interleaved
        if self.existing_printer:
            self.existing_printer.thread.join()
        self._write_header()

        while True:
            message_type, message = self.proc.output.get()

            if message_type == 'eof':
                break

            if message_type == 'stdout':
                output = message

            if message_type == 'stderr':
                output = message

            self._queue_write(output)

        self._write_footer()

        # Clear this panel printer from the registry
        _set_printer(self.window_id, None)

    def _queue_write(self, chars, content_separator=None, wait=False):
        """
        Runs a callback in the UI thread to actually print output to the output
        panel

        :param chars:
            A unicode string to write to the panel

        :param content_separator:
            None, or a unicode string of character to ensure occurs right before
            the chars, unless the panel is empty

        :param wait:
            If the function should not return until the write to the panel has
            completed
        """

        event = None
        if wait:
            event = threading.Event()

        sublime.set_timeout(lambda: self._do_write(chars, content_separator, event), 1)

        if wait:
            event.wait()

    def _do_write(self, chars, content_separator, event):
        """
        Used with Sublime Text 2 since the "insert" command does not properly
        handle newline characters

        :param chars:
            A unicode string to write to the panel

        :param content_separator:
            None, or a unicode string of character to ensure occurs right before
            the chars, unless the panel is empty

        :param event:
            None, or a threading.Event() object that should be set once the
            write completes
        """

        if content_separator is not None and self.panel.size() > 0:
            end = self.panel.size()
            start = end - len(content_separator)
            if self.panel.substr(sublime.Region(start, end)) != content_separator:
                chars = content_separator + chars

        # In Sublime Text 2, the "insert" command does not handle newlines
        if sys.version_info < (3,):
            edit = self.panel.begin_edit('golang_panel_print', [])
            self.panel.insert(edit, self.panel.size(), chars)
            self.panel.end_edit(edit)
        else:
            self.panel.run_command('insert', {'characters': chars})

        if event:
            event.set()

    def _write_header(self):
        """
        Displays startup information about the process
        """

        title = ''

        env_vars = []
        for var_name in GO_ENV_VARS:
            var_key = var_name if sys.version_info >= (3,) else var_name.encode('ascii')
            if var_key in self.proc.env:
                value = self.proc.env.get(var_key)
                if sys.version_info < (3,):
                    value = value.decode('utf-8')
                env_vars.append((var_name, value))
        if env_vars:
            title += '> Environment:\n'
            for var_name, value in env_vars:
                title += '>   %s=%s\n' % (var_name, value)

        title += '> Directory: %s\n' % self.proc.cwd
        title += '> Command: %s\n' % subprocess.list2cmdline(self.proc.args)
        title += '> Output:\n'

        self._queue_write(title, content_separator='\n\n')

    def _write_footer(self):
        """
        Displays result information about the process, blocking until the
        write is completed
        """

        formatted_result = self.proc.result.title()
        runtime = self.proc.finished - self.proc.started

        output = '> Elapsed: %0.3fs\n> Result: %s' % (runtime, formatted_result)

        self._queue_write(output, content_separator='\n', wait=True)
        self.finished = True
        package_events.notify(
            'Golang Build',
            'build_complete',
            BuildCompleteEvent(
                task='',
                args=list(self.proc.args),
                working_dir=self.proc.cwd,
                env=self.proc.env.copy(),
                runtime=runtime,
                result=self.proc.result
            )
        )


BuildCompleteEvent = collections.namedtuple(
    'BuildCompleteEvent',
    [
        'task',
        'args',
        'working_dir',
        'env',
        'runtime',
        'result',
    ]
)


def _run_process(task, window, args, cwd, env):
    """
    Starts a GolangProcess() and creates a GolangPanelPrinter() for it

    :param task:
        A unicode string of the build task name - one of "build", "test",
        "cross_compile", "install", "clean", "get"

    :param window:
        A sublime.Window object of the window to display the output panel in

    :param args:
        A list of strings (unicode for Python 3, byte string for Python 2)
        of the process path and any arguments passed to it

    :param cwd:
        A unicode string of the working directory for the process

    :param env:
        A dict of strings (unicode for Python 3, byte string for Python 2)
        to pass to the process as the environment variables

    :return:
        A GolangProcess() object
    """

    window_id = window.id()

    proc = GolangProcess(args, cwd, env)

    existing_printer = _get_printer(window_id)

    # Calling sublime.Window.get_output_pane() clears the output panel, so we
    # only call it if there is not a running build, otherwise the output of the
    # cancelled build would be wiped from the screen, which makes it unclear
    # what the result of the interrupted build was. Additionally, if the
    # results of the previous build are still being displayed, we want to wait
    # on that thread so the output of the two is not interleaved.
    if existing_printer and not existing_printer.finished:
        panel = existing_printer.panel

    else:
        panel = window.get_output_panel('golang_build')
        existing_printer = None

    printer = GolangPanelPrinter(proc, panel, window_id, existing_printer)
    _set_printer(window_id, printer)

    window.run_command('show_panel', {'panel': 'output.golang_build'})

    return proc


def _set_proc(window, proc):
    """
    Sets the GolangProcess() object associated with a sublime.Window

    :param window:
        A sublime.Window object

    :param proc:
        A GolangProcess() object that is being run for the window
    """

    _PROCS[window.id()] = proc


def _get_proc(window):
    """
    Returns the GolangProcess() object associated with a sublime.Window

    :param window:
        A sublime.Window object

    :return:
        None or a GolangProcess() object. The GolangProcess() may or may not
        still be running.
    """

    return _PROCS.get(window.id())


def _set_printer(window_id, printer):
    """
    Sets the GolangPanelPrinter() object associated with a sublime.Window

    :param window_id:
        An integer of the window's id

    :param printer:
        A GolangPanelPrinter() object that is being run for the window
    """

    _PRINTERS[window_id] = printer


def _get_printer(window_id):
    """
    Returns the GolangPanelPrinter() object associated with a sublime.Window

    :param window_id:
        An integer of the window's id

    :return:
        None or a GolangPanelPrinter() object
    """

    return _PRINTERS.get(window_id)


def _format_message(string):
    """
    Takes a multi-line string and does the following:

     - dedents
     - converts newlines with text before and after into a single line
     - strips leading and trailing whitespace

    :param string:
        The string to format

    :return:
        The formatted string
    """

    output = textwrap.dedent(string)

    # Unwrap lines, taking into account bulleted lists, ordered lists and
    # underlines consisting of = signs
    if output.find('\n') != -1:
        output = re.sub('(?<=\\S)\n(?=[^ \n\t\\d\\*\\-=])', ' ', output)

    return output.strip()
