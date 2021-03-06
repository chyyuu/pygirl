from _ctypes.dummy import resize
from _ctypes.basics import _CData, sizeof, alignment, byref, addressof,\
     ArgumentError
from _ctypes.primitive import _SimpleCData
from _ctypes.pointer import _Pointer, _cast_addr
from _ctypes.function import CFuncPtr
from _ctypes.dll import dlopen
from _ctypes.structure import Structure
from _ctypes.array import Array
from _ctypes.builtin import _memmove_addr, _string_at_addr, _memset_addr,\
     set_conversion_mode, _wstring_at_addr
from _ctypes.union import Union

__version__ = '1.0.2'
#XXX platform dependant?
RTLD_LOCAL = 0
RTLD_GLOBAL = 256
FUNCFLAG_CDECL = 1
FUNCFLAG_PYTHONAPI = 4
