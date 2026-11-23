# Versioning

The package versioning policy is based on [Calendar Versioning](https://calver.org/),
with the following generic schemes:

| Stable release | Development release |
|----------------|---------------------|
| YYYY.MM.DD.Rev | YYYY.MM.DD.Rev.dev0 |

The meaning of each segment in the schemes is:
- YYYY - Full year - 2006, 2016, 2106
- MM - Short month - 1, 2, ..., 13
- DD - Short day - 1, 2, ..., 31
- Rev - It is an integer which starts from 1 and is used to ensure that
each build has a unique name. When a build starts, if nothing else in
the build number has changed, the Rev integer value is incremented by
one.
- dev0 - It is used to indicate a development release and is a suffix
that will be added to builds that are created in the feature branch.
