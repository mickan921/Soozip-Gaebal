"""The deliberately small, index-only requirements input language."""
from pathlib import Path
from packaging.requirements import InvalidRequirement, Requirement

PYTHON_VERSIONS = ('3.11', '3.12', '3.13', '3.14')


def read_requirements(values, files=()) -> tuple[str, ...]:
    sources = [('Requirements', '\n'.join(values))]
    sources.extend((str(path), Path(path).read_text(encoding='utf-8')) for path in files)
    result = []
    for source, content in sources:
        for number, line in enumerate(content.splitlines(), 1):
            line = line.split('#', 1)[0].strip()
            if not line:
                continue
            try:
                requirement = Requirement(line)
                if requirement.url or line.startswith('-'):
                    raise ValueError('Only PyPI requirement specifiers are supported')
            except (InvalidRequirement, ValueError) as exc:
                raise ValueError(f'{source}:{number}: Invalid PyPI requirement {line!r}. '
                                 'Use one name, version constraint, extras, or marker per line; '
                                 'options, URLs, and local projects are unsupported.') from exc
            normalized = str(requirement)
            if normalized not in result:
                result.append(normalized)
    if not result:
        raise ValueError('Enter at least one Python requirement.')
    return tuple(result)
