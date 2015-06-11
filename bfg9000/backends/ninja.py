import os
import re
import sys
from cStringIO import StringIO
from collections import namedtuple, OrderedDict
from itertools import chain

from .. import path
from .. import safe_str
from .. import shell
from .. import utils

_rule_handlers = {}
def rule_handler(rule_name):
    def decorator(fn):
        _rule_handlers[rule_name] = fn
        return fn
    return decorator

NinjaRule = namedtuple('NinjaRule', ['command', 'depfile', 'deps', 'generator'])
NinjaBuild = namedtuple('NinjaBuild', ['outputs', 'rule', 'inputs', 'implicit',
                                       'order_only', 'variables'])

class NinjaWriter(object):
    def __init__(self, stream):
        self.stream = stream

    @staticmethod
    def escape_str(string, syntax):
        if syntax == 'output':
            return re.sub(r'([:$\n ])', r'$\1', string)
        elif syntax == 'input' or syntax == 'variable':
            return re.sub(r'([$\n ])', r'$\1', string)
        elif syntax == 'shell_line':
            return string.replace('$', '$$')
        elif syntax == 'shell_word':
            return shell.quote(string).replace('$', '$$')
        else:
            raise ValueError('unknown syntax "{}"'.format(syntax))

    def write_literal(self, string):
        self.stream.write(string)

    def write(self, thing, syntax):
        thing = safe_str.safe_str(thing)

        if isinstance(thing, basestring):
            self.write_literal(self.escape_str(thing, syntax))
        elif isinstance(thing, safe_str.escaped_str):
            self.write_literal(thing.string)
        elif isinstance(thing, path.real_path):
            if thing.base != 'builddir':
                self.write(_path_vars[thing.base], syntax)
                self.write_literal(os.sep)
            self.write(thing.path, syntax)
        elif isinstance(thing, safe_str.jbos):
            for j in thing.bits:
                self.write(j, syntax)
        else:
            raise TypeError(type(thing))

    def write_each(self, things, syntax, delim=' ', prefix=None, suffix=None):
        for tween, i in utils.tween(things, delim, prefix, suffix):
            self.write_literal(i) if tween else self.write(i, syntax)

    def write_shell(self, thing):
        if utils.isiterable(thing):
            self.write_each(thing, 'shell_word')
        else:
            self.write(thing, 'shell_line')

class NinjaVariable(object):
    def __init__(self, name):
        self.name = re.sub('\W', '_', name)

    def use(self):
        return safe_str.escaped_str('${}'.format(self.name))

    def _safe_str(self):
        return self.use()

    def __str__(self):
        raise NotImplementedError()

    def __repr__(self):
        return repr(self.use())

    def __hash__(self):
        return hash(self.name)

    def __cmp__(self, rhs):
        return cmp(self.name, rhs.name)

    def __add__(self, rhs):
        return self.use() + rhs

    def __radd__(self, lhs):
        return lhs + self.use()

var = NinjaVariable

_path_vars = {
    'srcdir': NinjaVariable('srcdir'),
    'prefix': NinjaVariable('prefix'),
}
class NinjaFile(object):
    def __init__(self):
        # TODO: Sort variables in some useful order
        self._variables = OrderedDict()
        self._rules = OrderedDict()
        self._builds = []
        self._build_outputs = set()
        self._defaults = []

    def variable(self, name, value, syntax='variable'):
        if not isinstance(name, NinjaVariable):
            name = NinjaVariable(name)
        if self.has_variable(name):
            raise ValueError('variable "{}" already exists'.format(name))
        self._variables[name] = (value, syntax)
        return name

    def has_variable(self, name):
        if not isinstance(name, NinjaVariable):
            name = NinjaVariable(name)
        return name in self._variables

    def rule(self, name, command, depfile=None, deps=None, generator=False):
        if re.search('\W', name):
            raise ValueError('rule name contains invalid characters')
        if self.has_rule(name):
            raise ValueError('rule "{}" already exists'.format(name))
        self._rules[name] = NinjaRule(command, depfile, deps, generator)

    def has_rule(self, name):
        return name in self._rules

    def build(self, output, rule, inputs=None, implicit=None, order_only=None,
              variables=None):
        if rule != 'phony' and not self.has_rule(rule):
            raise ValueError('unknown rule "{}"'.format(rule))

        real_variables = {}
        if variables:
            for k, v in variables.iteritems():
                if not isinstance(k, NinjaVariable):
                    k = NinjaVariable(k)
                real_variables[k] = v

        outputs = utils.listify(output)
        for i in outputs:
            if self.has_build(i):
                raise ValueError('build for "{}" already exists'.format(i))
            self._build_outputs.add(i)
        self._builds.append(NinjaBuild(
            outputs, rule, utils.listify(inputs), utils.listify(implicit),
            utils.listify(order_only), real_variables
        ))

    def has_build(self, name):
        return name in self._build_outputs

    def default(self, paths):
        self._defaults.extend(paths)

    def _write_variable(self, out, name, value, indent=0, syntax='variable'):
        out.write_literal(('  ' * indent) + name.name + ' = ')
        out.write_each(utils.iterate(value), syntax)
        out.write_literal('\n')

    def _write_rule(self, out, name, rule):
        out.write_literal('rule ' + name + '\n')

        self._write_variable(out, var('command'), rule.command, 1, 'shell_word')
        if rule.depfile:
            self._write_variable(out, var('depfile'), rule.depfile, 1)
        if rule.deps:
            self._write_variable(out, var('deps'), rule.deps, 1)
        if rule.generator:
            self._write_variable(out, var('generator'), '1', 1)

    def _write_build(self, out, build):
        out.write_literal('build ')
        out.write_each(build.outputs, syntax='output')
        out.write_literal(': ' + build.rule)

        out.write_each(build.inputs, syntax='input', prefix=' ')
        out.write_each(build.implicit, syntax='input', prefix=' | ')
        out.write_each(build.order_only, syntax='input', prefix=' || ')
        out.write_literal('\n')

        if build.variables:
            for k, v in build.variables.iteritems():
                self._write_variable(out, k, v, 1, 'shell_word')

    def write(self, out):
        out = NinjaWriter(out)

        for name, (value, syntax) in self._variables.iteritems():
            self._write_variable(out, name, value, syntax=syntax)
        if self._variables:
            out.write_literal('\n')

        for name, rule in self._rules.iteritems():
            self._write_rule(out, name, rule)
            out.write_literal('\n')

        for build in self._builds:
            self._write_build(out, build)

        if self._defaults:
            out.write_literal('\ndefault ')
            out.write_each(self._defaults, syntax='input')
            out.write_literal('\n')

def write(env, build_inputs):
    buildfile = NinjaFile()
    buildfile.variable(_path_vars['srcdir'], env.srcdir)

    all_rule(build_inputs.get_default_targets(), buildfile)
    install_rule(build_inputs.install_targets, buildfile, env)
    test_rule(build_inputs.tests, build_inputs.test_targets, buildfile)
    for e in build_inputs.edges:
        _rule_handlers[type(e).__name__](e, build_inputs, buildfile)
    regenerate_rule(buildfile, env)

    with open(os.path.join(env.builddir, 'build.ninja'), 'w') as out:
        buildfile.write(out)

def cmd_var(compiler, buildfile):
    var = NinjaVariable(compiler.command_var)
    if not buildfile.has_variable(var):
        buildfile.variable(var, compiler.command_name)
    return var

def flags_vars(name, value, buildfile):
    global_flags = NinjaVariable('global_{}'.format(name))
    if not buildfile.has_variable(global_flags):
        buildfile.variable(global_flags, value, syntax='shell_word')

    flags = NinjaVariable('{}'.format(name))
    if not buildfile.has_variable(flags):
        buildfile.variable(flags, global_flags, syntax='shell_word')

    return global_flags, flags

def all_rule(default_targets, buildfile):
    buildfile.default(['all'])
    buildfile.build(
        output='all',
        rule='phony',
        inputs=[i.path for i in default_targets]
    )

def chain_commands(commands, delim=' && '):
    out = NinjaWriter(StringIO())
    for tween, line in utils.tween(commands, delim):
        out.write_literal(line) if tween else out.write_shell(line)
    return safe_str.escaped_str(out.stream.getvalue())

# TODO: Write a better `install` program to simplify this
def install_rule(install_targets, buildfile, env):
    if not install_targets:
        return

    buildfile.variable(_path_vars['prefix'], env.install_prefix)

    def install_cmd(kind):
        install = NinjaVariable('install')
        if not buildfile.has_variable(install):
            buildfile.variable(install, 'install', syntax='shell_word')

        if kind == 'program':
            install_program = NinjaVariable('install_program')
            if not buildfile.has_variable(install_program):
                buildfile.variable(install_program, install)
            return install_program
        else:
            install_data = NinjaVariable('install_data')
            if not buildfile.has_variable(install_data):
                buildfile.variable(install_data, [install, '-m', '644'],
                                   syntax='shell_word')
            return install_data

    if not buildfile.has_rule('command'):
        buildfile.rule(name='command', command=var('cmd'))

    def install_line(file):
        src = file.path.local_path()
        dst = file.path.install_path()
        return [ install_cmd(file.install_kind), '-D', src, dst ]

    def mkdir_line(dir):
        src = dir.path.append('*').local_path()
        dst = dir.path.parent().install_path()
        return 'mkdir -p ' + dst + ' && cp -r ' + src + ' ' + dst

    commands = chain((install_line(i) for i in install_targets.files),
                     (mkdir_line(i) for i in install_targets.directories))
    buildfile.build(
        output='install',
        rule='command',
        implicit=['all'],
        variables={'cmd': chain_commands(commands)}
    )

def test_rule(tests, test_targets, buildfile):
    if not test_targets:
        return

    buildfile.build(
        output='tests',
        rule='phony',
        inputs=[i.path for i in test_targets]
    )

    def build_commands(tests, collapse=False):
        cmd, deps = [], []
        def command(subcmd):
            if collapse:
                out = NinjaWriter(StringIO())
                out.write_each(subcmd, 'shell_word')
                return safe_str.escaped_str(shell.quote(out.stream.getvalue()))
            return subcmd

        for i in tests:
            if type(i).__name__ == 'TestDriver':
                args, moredeps = build_commands(i.tests, True)
                deps += [i.driver.path] + moredeps
                cmd.append(command([i.driver.path] + i.options + args))
            else:
                cmd.append(command([i.test.path] + i.options))
        return cmd, deps

    commands, deps = build_commands(tests)
    if not buildfile.has_rule('command'):
        buildfile.rule(name='command', command=var('cmd'))
    buildfile.build(
        output='test',
        rule='command',
        inputs=['tests'] + deps,
        variables={'cmd': chain_commands(commands)}
    )

def regenerate_rule(buildfile, env):
    buildfile.rule(
        name='regenerate',
        command=[env.bfgpath, '--regenerate', '.'],
        generator=True
    )
    buildfile.build(
        output=path.Path('build.ninja', path.Path.builddir, path.Path.basedir),
        rule='regenerate',
        implicit=[path.Path('build.bfg', path.Path.srcdir, path.Path.basedir)]
    )

@rule_handler('Compile')
def emit_object_file(rule, build_inputs, buildfile):
    compiler = rule.builder
    global_cflags, cflags = flags_vars(
        compiler.command_var + 'flags',
        compiler.global_args +
          build_inputs.global_options.get(rule.file.lang, []),
        buildfile
    )

    variables = {}

    cflags_value = []
    if rule.in_shared_library:
        cflags_value.extend(compiler.library_args)
    cflags_value.extend(chain.from_iterable(
        compiler.include_dir(i) for i in rule.include
    ))
    cflags_value.extend(rule.options)
    if cflags_value:
        variables[cflags] = [global_cflags] + cflags_value

    if not buildfile.has_rule(compiler.name):
        command_kwargs = {}
        depfile = None
        deps = None
        if compiler.deps_flavor == 'gcc':
            deps = 'gcc'
            command_kwargs['deps'] = depfile = var('out') + '.d'
        elif compiler.deps_flavor == 'msvc':
            deps = 'msvc'
            command_kwargs['deps'] = True

        buildfile.rule(name=compiler.name, command=compiler.command(
            cmd=cmd_var(compiler, buildfile), input=var('in'),
            output=var('out'), args=cflags, **command_kwargs
        ), depfile=depfile, deps=deps)

    buildfile.build(
        output=rule.target.path,
        rule=compiler.name,
        inputs=[rule.file.path],
        implicit=[i.path for i in rule.extra_deps],
        variables=variables
    )

@rule_handler('Link')
def emit_link(rule, build_inputs, buildfile):
    linker = rule.builder
    global_ldflags, ldflags = flags_vars(
        linker.link_var + 'flags', linker.global_args, buildfile
    )

    variables = {}
    command_kwargs = {}
    ldflags_value = list(linker.mode_args)
    lib_deps = [i for i in rule.libs if i.creator]

    path = utils.first(rule.target).path
    variables[var('output')] = path

    if linker.mode != 'static_library':
        ldflags_value.extend(rule.options)
        ldflags_value.extend(linker.lib_dirs(lib_deps))

        target_dirname = path.parent().local_path().path
        ldflags_value.extend(linker.rpath(
            # TODO: Provide a relpath function for Path objects?
            os.path.relpath(i.path.parent().local_path().path, target_dirname)
            for i in lib_deps
        ))

        global_ldlibs, ldlibs = flags_vars(
            linker.link_var + 'libs', linker.global_libs, buildfile
        )
        command_kwargs['libs'] = ldlibs
        if rule.libs:
            variables[ldlibs] = [global_ldlibs] + list(chain.from_iterable(
                linker.link_lib(i) for i in rule.libs
            ))

    if linker.mode == 'shared_library' and utils.isiterable(rule.target):
        ldflags_value.extend(linker.import_lib(rule.target[1]))

    if ldflags_value:
        variables[ldflags] = [global_ldflags] + ldflags_value

    if not buildfile.has_rule(linker.name):
        buildfile.rule(name=linker.name, command=linker.command(
            cmd=cmd_var(linker, buildfile), input=var('in'),
            output=var('output'), args=ldflags, **command_kwargs
        ))

    buildfile.build(
        output=[i.path for i in utils.iterate(rule.target)],
        rule=linker.name,
        inputs=[i.path for i in rule.files],
        implicit=[i.path for i in chain(lib_deps, rule.extra_deps)],
        variables=variables
    )

@rule_handler('Alias')
def emit_alias(rule, build_inputs, buildfile):
    buildfile.build(
        output=rule.target.path,
        rule='phony',
        inputs=[i.path for i in rule.extra_deps]
    )

@rule_handler('Command')
def emit_command(rule, build_inputs, buildfile):
    if not buildfile.has_rule('command'):
        buildfile.rule(name='command', command=var('cmd'))

    e = safe_str.escaped_str
    buildfile.build(
        output=rule.target.path,
        rule='command',
        inputs=[i.path for i in rule.extra_deps],
        variables={'cmd': chain_commands(rule.cmds)}
    )