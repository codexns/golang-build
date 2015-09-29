# coding: utf-8
from __future__ import unicode_literals, division, absolute_import, print_function

import os
import sys
import locale

import golangconfig

if sys.version_info < (3,):
    golang_build = sys.modules['golang_build']
else:
    golang_build = sys.modules['Golang Build.golang_build']


class ShellenvMock():

    _env_encoding = locale.getpreferredencoding() if sys.platform == 'win32' else 'utf-8'
    _fs_encoding = 'mbcs' if sys.platform == 'win32' else 'utf-8'

    _shell = None
    _data = None

    def __init__(self, shell, data):
        self._shell = shell
        self._data = data

    def get_env(self, for_subprocess=False):
        if not for_subprocess or sys.version_info >= (3,):
            return (self._shell, self._data)

        shell = self._shell.encode(self._fs_encoding)
        env = {}
        for name, value in self._data.items():
            env[name.encode(self._env_encoding)] = value.encode(self._env_encoding)

        return (shell, env)

    def get_path(self):
        return (self._shell, self._data.get('PATH', '').split(os.pathsep))

    def env_encode(self, value):
        if sys.version_info >= (3,):
            return value
        return value.encode(self._env_encoding)

    def path_encode(self, value):
        if sys.version_info >= (3,):
            return value
        return value.encode(self._fs_encoding)

    def path_decode(self, value):
        if sys.version_info >= (3,):
            return value
        return value.decode(self._fs_encoding)


class GolangBuildMock():

    _shellenv = None

    _shell = None
    _env = None

    def __init__(self, shell, env):
        self._shell = shell
        self._env = env

    def __enter__(self):
        self._shellenv = golangconfig.shellenv
        golangconfig.shellenv = ShellenvMock(self._shell, self._env)
        golang_build.shellenv = golangconfig.shellenv
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        golangconfig.shellenv = self._shellenv
        golang_build.shellenv = self._shellenv
