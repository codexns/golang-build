# coding: utf-8
from __future__ import unicode_literals, division, absolute_import, print_function

import sys
import threading
import unittest
from os import path
import time

import sublime

import package_events

if sys.version_info < (3,):
    from Queue import Queue
else:
    from queue import Queue


TEST_GOPATH = path.join(path.dirname(__file__), 'go_projects')
VIEW_SETTINGS = {
    'GOPATH': TEST_GOPATH,
    'GOOS': None,
    'GOARCH': None,
    'GOARM': None,
    'GO386': None,
    'GORACE': None
}


class GolangBuildTests(unittest.TestCase):

    def test_build(self):
        ensure_not_ui_thread()

        file_path = path.join(TEST_GOPATH, 'src', 'good', 'hello.go')

        def _run_build(view, result_queue):
            view.window().run_command('golang_build')

        result_queue = open_file(file_path, VIEW_SETTINGS, _run_build)
        result = wait_build(result_queue)
        self.assertEqual('success', result)
        self.assertTrue(confirm_user('Did build succeed?'))

    def test_clean(self):
        ensure_not_ui_thread()

        file_path = path.join(TEST_GOPATH, 'src', 'good', 'hello.go')

        def _run_build(view, result_queue):
            view.window().run_command('golang_build', {'task': 'clean'})

        result_queue = open_file(file_path, VIEW_SETTINGS, _run_build)
        result = wait_build(result_queue)
        self.assertEqual('success', result)
        self.assertTrue(confirm_user('Did clean succeed?'))

    def test_test(self):
        ensure_not_ui_thread()

        file_path = path.join(TEST_GOPATH, 'src', 'good', 'hello.go')

        def _run_build(view, result_queue):
            view.window().run_command('golang_build', {'task': 'test'})

        result_queue = open_file(file_path, VIEW_SETTINGS, _run_build)
        result = wait_build(result_queue)
        self.assertEqual('success', result)
        self.assertTrue(confirm_user('Did tests succeed?'))

    def test_install(self):
        ensure_not_ui_thread()

        file_path = path.join(TEST_GOPATH, 'src', 'good', 'hello.go')

        def _run_build(view, result_queue):
            view.window().run_command('golang_build', {'task': 'install'})

        result_queue = open_file(file_path, VIEW_SETTINGS, _run_build)
        result = wait_build(result_queue)
        self.assertEqual('success', result)
        self.assertTrue(confirm_user('Did install succeed?'))

    def test_cross_compile(self):
        ensure_not_ui_thread()

        file_path = path.join(TEST_GOPATH, 'src', 'good', 'hello.go')
        begin_event = threading.Event()

        def _run_build(view, result_queue):
            sublime.ok_cancel_dialog('Select linux/amd64 from quick panel', 'Ok')
            begin_event.set()
            view.window().run_command('golang_build', {'task': 'cross_compile'})

        result_queue = open_file(file_path, VIEW_SETTINGS, _run_build)
        begin_event.wait()
        result = wait_build(result_queue, timeout=15)
        self.assertEqual('success', result)
        self.assertTrue(confirm_user('Did cross-compile succeed?'))

    def test_get(self):
        ensure_not_ui_thread()

        file_path = path.join(TEST_GOPATH, 'src', 'good', 'hello.go')
        begin_event = threading.Event()

        def _run_build(view, result_queue):
            sublime.set_clipboard('github.com/golang/example/hello')
            sublime.ok_cancel_dialog('Paste from the clipboard into the input panel', 'Ok')
            begin_event.set()
            view.window().run_command('golang_build_get')

        result_queue = open_file(file_path, VIEW_SETTINGS, _run_build)
        begin_event.wait()
        result = wait_build(result_queue)
        self.assertEqual('success', result)
        self.assertTrue(confirm_user('Did get succeed?'))

    def test_terminal(self):
        ensure_not_ui_thread()

        file_path = path.join(TEST_GOPATH, 'src', 'good', 'hello.go')

        def _run_build(view, result_queue):
            view.window().run_command('golang_build_terminal')

        open_file(file_path, VIEW_SETTINGS, _run_build)
        self.assertTrue(confirm_user('Did terminal open?'))

    def test_build_bad(self):
        ensure_not_ui_thread()

        file_path = path.join(TEST_GOPATH, 'src', 'bad', 'hello.go')

        def _run_build(view, result_queue):
            view.window().run_command('golang_build')

        result_queue = open_file(file_path, VIEW_SETTINGS, _run_build)
        result = wait_build(result_queue)
        self.assertEqual('error', result)
        self.assertTrue(confirm_user('Did build fail?'))

    def test_build_cancel(self):
        ensure_not_ui_thread()

        file_path = path.join(TEST_GOPATH, 'src', 'good', 'hello.go')

        def _run_build(view, result_queue):
            view.window().run_command('golang_build')

            def _cancel_build():
                view.window().run_command('golang_build_cancel')

            sublime.set_timeout(_cancel_build, 50)

        result_queue = open_file(file_path, VIEW_SETTINGS, _run_build)
        result = wait_build(result_queue)
        self.assertEqual('cancelled', result)
        self.assertTrue(confirm_user('Was build cancelled?'))

    def test_build_reopen(self):
        ensure_not_ui_thread()

        file_path = path.join(TEST_GOPATH, 'src', 'good', 'hello.go')

        def _run_build(view, result_queue):
            view.window().run_command('golang_build')

        result_queue = open_file(file_path, VIEW_SETTINGS, _run_build)
        result = wait_build(result_queue)
        self.assertEqual('success', result)

        time.sleep(0.4)

        def _hide_panel():
            sublime.active_window().run_command('hide_panel')
        sublime.set_timeout(_hide_panel, 1)

        time.sleep(0.4)
        self.assertTrue(confirm_user('Was build output hidden?'))

        def _reopen_panel():
            sublime.active_window().run_command('golang_build_reopen')
        sublime.set_timeout(_reopen_panel, 1)

        time.sleep(0.4)
        self.assertTrue(confirm_user('Was build output reopened?'))

    def test_build_interrupt(self):
        ensure_not_ui_thread()

        file_path = path.join(TEST_GOPATH, 'src', 'good', 'hello.go')
        begin_event = threading.Event()
        second_begin_event = threading.Event()

        def _run_build(view, result_queue):
            sublime.ok_cancel_dialog('Press the "Stop Running Build" button when prompted', 'Ok')

            begin_event.set()
            view.window().run_command('golang_build')

            def _new_build():
                view.window().run_command('golang_build')
                second_begin_event.set()

            sublime.set_timeout(_new_build, 50)

        # We perform a cross-compile so the user has time to interrupt the build
        custom_view_settings = VIEW_SETTINGS.copy()
        custom_view_settings['GOOS'] = 'linux'
        custom_view_settings['GOARCH'] = 'amd64'

        result_queue = open_file(file_path, custom_view_settings, _run_build)
        begin_event.wait()
        result1 = wait_build(result_queue)
        self.assertEqual('cancelled', result1)
        second_begin_event.wait()
        result2 = wait_build(result_queue)
        self.assertEqual('success', result2)
        self.assertTrue(confirm_user('Was the first build cancelled and the second successful?'))


def ensure_not_ui_thread():
    if isinstance(threading.current_thread(), threading._MainThread):
        raise RuntimeError('Tests can not be run in the UI thread')


def open_file(file_path, view_settings, callback):
    result_queue = Queue()

    def open_file_callback():
        window = sublime.active_window()
        window.run_command(
            'open_file',
            {
                'file': file_path
            }
        )

        when_file_opened(window, file_path, view_settings, callback, result_queue)
    sublime.set_timeout(open_file_callback, 50)
    return result_queue


def when_file_opened(window, file_path, view_settings, callback, result_queue):
    view = window.active_view()
    if view and view.file_name() == file_path:
        view.settings().set('golang', view_settings)
        callback(view, result_queue)
        return
    # If the view was not ready, retry a short while later
    sublime.set_timeout(lambda: when_file_opened(window, file_path, view_settings, callback, result_queue), 50)


def wait_build(result_queue, timeout=5):
    def _send_result(package_name, event_name, payload):
        result_queue.put(payload.result)

    try:
        package_events.listen('Golang Build', _send_result)
        return result_queue.get(timeout=timeout)
    finally:
        package_events.unlisten('Golang Build', _send_result)


def confirm_user(message):
    queue = Queue()
    def _show_ok_cancel():
        response = sublime.ok_cancel_dialog(message, 'Yes')
        queue.put(response)
    sublime.set_timeout(_show_ok_cancel, 1)
    return queue.get()
