import os

from .. import safe_str
from ..builtins.write_file import WriteFile
from ..file_types import *
from ..iterutils import iterate, uniques
from ..path import Path
from ..shell import shell_list


class JvmBuilder(object):
    def __init__(self, env, lang, name, command, jar_command, flags_name,
                 flags):
        self.brand = 'jvm'  # XXX: Be more specific?
        self.compiler = JvmCompiler(env, lang, name, command, flags_name,
                                    flags)

        linker = JarMaker(env, jar_command)
        self._linkers = {
            'executable': linker,
            'shared_library': linker,
        }

    @property
    def flavor(self):
        return 'jvm'

    def linker(self, mode):
        return self._linkers[mode]


class JvmCompiler(object):
    def __init__(self, env, lang, name, command, flags_name, flags):
        self.env = env
        self.lang = lang

        self.rule_name = self.command_var = name
        self.command = command

        self.flags_var = flags_name
        self.global_args = flags

    @property
    def deps_flavor(self):
        return None

    @property
    def num_outputs(self):
        return 1

    @property
    def depends_on_libs(self):
        return True

    def __call__(self, cmd, input, output, args=None):
        jvmoutput = self.env.tool('jvmoutput')

        result = shell_list([cmd])
        result.extend(iterate(args))
        result.extend(['-verbose', '-d', '.'])
        result.extend([input, safe_str.escaped_str('2>&1 |')] +
                      jvmoutput(jvmoutput.command, output))
        return result

    def _class_path(self, libraries):
        dirs = uniques(i.path for i in iterate(libraries))
        if dirs:
            return ['-cp', safe_str.join(dirs, os.pathsep)]
        return []

    def args(self, options, output, pkg=False):
        libraries = getattr(options, 'libs', [])
        return self._class_path(libraries)

    def link_args(self, mode, defines):
        return []

    def output_file(self, name, options):
        return JvmClassList(Path(name + '.classlist'), 'jvm', self.lang)


class JarMaker(object):
    rule_name = command_var = 'jar'
    flags_var = 'jarflags'
    libs_var = 'jarlibs'

    def __init__(self, env, command):
        self.command = command

        self.global_args = []
        self.global_libs = []

    @property
    def flavor(self):
        return 'jar'

    def can_link(self, format, langs):
        return format == 'jvm'

    @property
    def num_outputs(self):
        return 1

    def pre_build(self, build, options, name):
        dirs = uniques(i.path for i in iterate(options.libs))
        text = ['Class-Path: {}'.format(
            os.pathsep.join(i.basename() for i in dirs)
        )]
        if getattr(options, 'entry_point', None):
            text.append('Main-Class: {}'.format(options.entry_point))

        source = File(Path(name + '-manifest.txt'))
        WriteFile(build, source, text)
        options.manifest = source

    def __call__(self, cmd, input, output, manifest, libs=None, args=None):
        result = [cmd, 'cfm', output, manifest]
        result.extend(iterate(input))
        return result

    def transform_input(self, input):
        return ['@' + safe_str.safe_str(i) if isinstance(i, JvmClassList)
                else i for i in input]

    def args(self, options, output, pkg=False):
        return []

    def always_libs(self, primary):
        return []

    def libs(self, options, output, pkg=False):
        return []

    def output_file(self, name, options):
        return ExecutableLibrary(Path(name + '.jar'), 'jvm')
