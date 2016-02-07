from .hooks import builtin
from ..backends.make import writer as make
from ..backends.ninja import writer as ninja
from ..build_inputs import Edge
from ..file_types import Phony


class Alias(Edge):
    def __init__(self, build, name, deps=None):
        Edge.__init__(self, build, Phony(name), deps)


@builtin.globals('build_inputs')
def alias(build, *args, **kwargs):
    return Alias(build, *args, **kwargs).public_output


@make.rule_handler(Alias)
def make_alias(rule, build_inputs, buildfile, env):
    buildfile.rule(
        target=rule.output,
        deps=rule.extra_deps,
        phony=True
    )


@ninja.rule_handler(Alias)
def ninja_alias(rule, build_inputs, buildfile, env):
    buildfile.build(
        output=rule.output,
        rule='phony',
        inputs=rule.extra_deps
    )

try:
    from ..backends.msbuild import writer as msbuild

    import warnings

    @msbuild.rule_handler(Alias)
    def msbuild_alias(rule, build_inputs, solution, env):
        warnings.warn('alias rules not currently supported with msbuild')
except:
    pass
