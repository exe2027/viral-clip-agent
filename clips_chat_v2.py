#!/usr/bin/env python3
"""DEPRECADO (Fase 5.1): este módulo vive ahora en clipagent.ingest.

Se mantiene como shim para no romper scripts externos que lo
importen por su nombre viejo. El código nuevo usa clipagent.
"""
import sys as _sys
import warnings as _warnings

_warnings.warn("clips_chat_v2.py está deprecado; usa clipagent.ingest",
                DeprecationWarning, stacklevel=2)

from clipagent import ingest as _new
# Alias completo: `import layout` devuelve el módulo nuevo,
# con todos sus atributos (también los privados).
_sys.modules[__name__] = _new

if __name__ == "__main__":
    raise SystemExit(_new.main())
