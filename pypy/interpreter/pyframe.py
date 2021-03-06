""" PyFrame class implementation with the interpreter main loop.
"""

from pypy.tool.pairtype import extendabletype
from pypy.interpreter import eval, baseobjspace, pycode
from pypy.interpreter.argument import Arguments, ArgumentsFromValuestack
from pypy.interpreter.error import OperationError
from pypy.interpreter import pytraceback
import opcode
from pypy.rlib.objectmodel import we_are_translated, instantiate
from pypy.rlib.jit import we_are_jitted, hint
from pypy.rlib import rstack # for resume points


# Define some opcodes used
g = globals()
for op in '''DUP_TOP POP_TOP SETUP_LOOP SETUP_EXCEPT SETUP_FINALLY
POP_BLOCK END_FINALLY'''.split():
    g[op] = opcode.opmap[op]
HAVE_ARGUMENT = opcode.HAVE_ARGUMENT


class PyFrame(eval.Frame):
    """Represents a frame for a regular Python function
    that needs to be interpreted.

    See also pyopcode.PyStandardFrame and pynestedscope.PyNestedScopeFrame.

    Public fields:
     * 'space' is the object space this frame is running in
     * 'code' is the PyCode object this frame runs
     * 'w_locals' is the locals dictionary to use
     * 'w_globals' is the attached globals dictionary
     * 'builtin' is the attached built-in module
     * 'valuestack_w', 'blockstack', control the interpretation
    """

    __metaclass__ = extendabletype

    frame_finished_execution = False
    last_instr               = -1
    last_exception           = None
    f_back                   = None
    w_f_trace                = None
    # For tracing
    instr_lb                 = 0
    instr_ub                 = -1
    instr_prev               = -1

    def __init__(self, space, code, w_globals, closure):
        self = hint(self, access_directly=True)
        assert isinstance(code, pycode.PyCode)
        self.pycode = code
        eval.Frame.__init__(self, space, w_globals, code.co_nlocals)
        self.valuestack_w = [None] * code.co_stacksize
        self.valuestackdepth = 0
        self.blockstack = []
        if space.config.objspace.honor__builtins__:
            self.builtin = space.builtin.pick_builtin(w_globals)
        # regular functions always have CO_OPTIMIZED and CO_NEWLOCALS.
        # class bodies only have CO_NEWLOCALS.
        self.initialize_frame_scopes(closure)
        self.fastlocals_w = [None]*self.numlocals
        self.f_lineno = self.pycode.co_firstlineno

    def get_builtin(self):
        if self.space.config.objspace.honor__builtins__:
            return self.builtin
        else:
            return self.space.builtin
        
    def initialize_frame_scopes(self, closure): 
        # regular functions always have CO_OPTIMIZED and CO_NEWLOCALS.
        # class bodies only have CO_NEWLOCALS.
        # CO_NEWLOCALS: make a locals dict unless optimized is also set
        # CO_OPTIMIZED: no locals dict needed at all
        # NB: this method is overridden in nestedscope.py
        flags = self.pycode.co_flags
        if flags & pycode.CO_OPTIMIZED: 
            return 
        if flags & pycode.CO_NEWLOCALS:
            self.w_locals = self.space.newdict()
        else:
            assert self.w_globals is not None
            self.w_locals = self.w_globals

    def run(self):
        """Start this frame's execution."""
        if self.pycode.co_flags & pycode.CO_GENERATOR:
            from pypy.interpreter.generator import GeneratorIterator
            return self.space.wrap(GeneratorIterator(self))
        else:
            return self.execute_frame()

    def execute_generator_frame(self, w_inputvalue):
        # opcode semantic change in CPython 2.5: we must pass an input value
        # when resuming a generator, which goes into the value stack.
        # (it's always w_None for now - not implemented in generator.py)
        if self.pycode.magic >= 0xa0df294 and self.last_instr != -1:
            self.pushvalue(w_inputvalue)
        return self.execute_frame()

    def execute_frame(self):
        """Execute this frame.  Main entry point to the interpreter."""
        executioncontext = self.space.getexecutioncontext()
        executioncontext.enter(self)
        try:
            executioncontext.call_trace(self)
            # Execution starts just after the last_instr.  Initially,
            # last_instr is -1.  After a generator suspends it points to
            # the YIELD_VALUE instruction.
            next_instr = self.last_instr + 1
            w_exitvalue = self.dispatch(self.pycode, next_instr,
                                        executioncontext)
            rstack.resume_point("execute_frame", self, executioncontext, returns=w_exitvalue)
            executioncontext.return_trace(self, w_exitvalue)
            # on exit, we try to release self.last_exception -- breaks an
            # obvious reference cycle, so it helps refcounting implementations
            self.last_exception = None
        finally:
            executioncontext.leave(self)
        return w_exitvalue
    execute_frame.insert_stack_check_here = True

    # stack manipulation helpers
    def pushvalue(self, w_object):
        depth = self.valuestackdepth
        self.valuestack_w[depth] = w_object
        self.valuestackdepth = depth + 1

    def popvalue(self):
        depth = self.valuestackdepth - 1
        assert depth >= 0, "pop from empty value stack"
        w_object = self.valuestack_w[depth]
        self.valuestack_w[depth] = None
        self.valuestackdepth = depth
        return w_object

    def popstrdictvalues(self, n):
        dic_w = {}
        while True:
            n -= 1
            if n < 0:
                break
            hint(n, concrete=True)
            w_value = self.popvalue()
            w_key   = self.popvalue()
            key = self.space.str_w(w_key)
            dic_w[key] = w_value
        return dic_w

    def popvalues(self, n):
        values_w = [None] * n
        while True:
            n -= 1
            if n < 0:
                break
            hint(n, concrete=True)
            values_w[n] = self.popvalue()
        return values_w

    def peekvalues(self, n):
        values_w = [None] * n
        base = self.valuestackdepth - n
        assert base >= 0
        while True:
            n -= 1
            if n < 0:
                break
            hint(n, concrete=True)
            values_w[n] = self.valuestack_w[base+n]
        return values_w

    def dropvalues(self, n):
        finaldepth = self.valuestackdepth - n
        assert finaldepth >= 0, "stack underflow in dropvalues()"        
        while True:
            n -= 1
            if n < 0:
                break
            hint(n, concrete=True)
            self.valuestack_w[finaldepth+n] = None
        self.valuestackdepth = finaldepth

    def pushrevvalues(self, n, values_w): # n should be len(values_w)
        while True:
            n -= 1
            if n < 0:
                break
            hint(n, concrete=True)
            self.pushvalue(values_w[n])

    def dupvalues(self, n):
        delta = n-1
        while True:
            n -= 1
            if n < 0:
                break
            hint(n, concrete=True)
            w_value = self.peekvalue(delta)
            self.pushvalue(w_value)
        
    def peekvalue(self, index_from_top=0):
        index = self.valuestackdepth + ~index_from_top
        assert index >= 0, "peek past the bottom of the stack"
        return self.valuestack_w[index]

    def settopvalue(self, w_object, index_from_top=0):
        index = self.valuestackdepth + ~index_from_top
        assert index >= 0, "settop past the bottom of the stack"
        self.valuestack_w[index] = w_object

    def dropvaluesuntil(self, finaldepth):
        depth = self.valuestackdepth - 1
        while depth >= finaldepth:
            self.valuestack_w[depth] = None
            depth -= 1
        self.valuestackdepth = finaldepth

    def savevaluestack(self):
        return self.valuestack_w[:self.valuestackdepth]

    def restorevaluestack(self, items_w):
        assert None not in items_w
        self.valuestack_w[:len(items_w)] = items_w
        self.dropvaluesuntil(len(items_w))

    def make_arguments(self, nargs):
        if we_are_jitted():
            return Arguments(self.space, self.peekvalues(nargs))
        else:
            return ArgumentsFromValuestack(self.space, self, nargs)
            
    def descr__reduce__(self, space):
        from pypy.interpreter.mixedmodule import MixedModule
        from pypy.module._pickle_support import maker # helper fns
        w_mod    = space.getbuiltinmodule('_pickle_support')
        mod      = space.interp_w(MixedModule, w_mod)
        new_inst = mod.get('frame_new')
        w        = space.wrap
        nt = space.newtuple

        cells = self._getcells()
        if cells is None:
            w_cells = space.w_None
        else:
            w_cells = space.newlist([space.wrap(cell) for cell in cells])

        if self.w_f_trace is None:
            f_lineno = self.get_last_lineno()
        else:
            f_lineno = self.f_lineno

        values_w = self.valuestack_w[0:self.valuestackdepth]
        w_valuestack = maker.slp_into_tuple_with_nulls(space, values_w)
        
        w_blockstack = nt([block._get_state_(space) for block in self.blockstack])
        w_fastlocals = maker.slp_into_tuple_with_nulls(space, self.fastlocals_w)
        tup_base = [
            w(self.pycode),
            ]

        if self.last_exception is None:
            w_exc_value = space.w_None
            w_tb = space.w_None
        else:
            w_exc_value = self.last_exception.w_value
            w_tb = w(self.last_exception.application_traceback)
        
        tup_state = [
            w(self.f_back),
            w(self.get_builtin()),
            w(self.pycode),
            w_valuestack,
            w_blockstack,
            w_exc_value, # last_exception
            w_tb,        #
            self.w_globals,
            w(self.last_instr),
            w(self.frame_finished_execution),
            w(f_lineno),
            w_fastlocals,
            space.w_None,           #XXX placeholder for f_locals
            
            #f_restricted requires no additional data!
            space.w_None, ## self.w_f_trace,  ignore for now

            w(self.instr_lb), #do we need these three (that are for tracing)
            w(self.instr_ub),
            w(self.instr_prev),
            w_cells,
            ]

        return nt([new_inst, nt(tup_base), nt(tup_state)])

    def descr__setstate__(self, space, w_args):
        from pypy.module._pickle_support import maker # helper fns
        from pypy.interpreter.pycode import PyCode
        from pypy.interpreter.module import Module
        args_w = space.unpackiterable(w_args)
        w_f_back, w_builtin, w_pycode, w_valuestack, w_blockstack, w_exc_value, w_tb,\
            w_globals, w_last_instr, w_finished, w_f_lineno, w_fastlocals, w_f_locals, \
            w_f_trace, w_instr_lb, w_instr_ub, w_instr_prev, w_cells = args_w

        new_frame = self
        pycode = space.interp_w(PyCode, w_pycode)

        if space.is_w(w_cells, space.w_None):
            closure = None
            cellvars = []
        else:
            from pypy.interpreter.nestedscope import Cell
            cells_w = space.unpackiterable(w_cells)
            cells = [space.interp_w(Cell, w_cell) for w_cell in cells_w]
            ncellvars = len(pycode.co_cellvars)
            cellvars = cells[:ncellvars]
            closure = cells[ncellvars:]
        
        # do not use the instance's __init__ but the base's, because we set
        # everything like cells from here
        PyFrame.__init__(self, space, pycode, w_globals, closure)
        new_frame.f_back = space.interp_w(PyFrame, w_f_back, can_be_None=True)
        new_frame.builtin = space.interp_w(Module, w_builtin)
        new_frame.blockstack = [unpickle_block(space, w_blk)
                                for w_blk in space.unpackiterable(w_blockstack)]
        values_w = maker.slp_from_tuple_with_nulls(space, w_valuestack)
        for w_value in values_w:
            new_frame.pushvalue(w_value)
        if space.is_w(w_exc_value, space.w_None):
            new_frame.last_exception = None
        else:
            from pypy.interpreter.pytraceback import PyTraceback
            tb = space.interp_w(PyTraceback, w_tb)
            new_frame.last_exception = OperationError(space.type(w_exc_value),
                                                      w_exc_value, tb
                                                      )
        new_frame.last_instr = space.int_w(w_last_instr)
        new_frame.frame_finished_execution = space.is_true(w_finished)
        new_frame.f_lineno = space.int_w(w_f_lineno)
        new_frame.fastlocals_w = maker.slp_from_tuple_with_nulls(space, w_fastlocals)

        if space.is_w(w_f_trace, space.w_None):
            new_frame.w_f_trace = None
        else:
            new_frame.w_f_trace = w_f_trace

        new_frame.instr_lb = space.int_w(w_instr_lb)   #the three for tracing
        new_frame.instr_ub = space.int_w(w_instr_ub)
        new_frame.instr_prev = space.int_w(w_instr_prev)

        self._setcellvars(cellvars)

    def hide(self):
        return self.pycode.hidden_applevel

    def getcode(self):
        return hint(hint(self.pycode, promote=True), deepfreeze=True)

    def getfastscope(self):
        "Get the fast locals as a list."
        return self.fastlocals_w

    def setfastscope(self, scope_w):
        """Initialize the fast locals from a list of values,
        where the order is according to self.pycode.signature()."""
        scope_len = len(scope_w)
        if scope_len > len(self.fastlocals_w):
            raise ValueError, "new fastscope is longer than the allocated area"
        self.fastlocals_w[:scope_len] = scope_w
        self.init_cells()

    def init_cells(self):
        """Initialize cellvars from self.fastlocals_w
        This is overridden in nestedscope.py"""
        pass
    
    def getclosure(self):
        return None

    def _getcells(self):
        return None

    def _setcellvars(self, cellvars):
        pass

    ### line numbers ###

    # for f*_f_* unwrapping through unwrap_spec in typedef.py

    def fget_f_lineno(space, self): 
        "Returns the line number of the instruction currently being executed."
        if self.w_f_trace is None:
            return space.wrap(self.get_last_lineno())
        else:
            return space.wrap(self.f_lineno)

    def fset_f_lineno(space, self, w_new_lineno):
        "Returns the line number of the instruction currently being executed."
        try:
            new_lineno = space.int_w(w_new_lineno)
        except OperationError, e:
            raise OperationError(space.w_ValueError,
                                 space.wrap("lineno must be an integer"))
            
        if self.w_f_trace is None:
            raise OperationError(space.w_ValueError,
                  space.wrap("f_lineo can only be set by a trace function."))

        if new_lineno < self.pycode.co_firstlineno:
            raise OperationError(space.w_ValueError,
                  space.wrap("line %d comes before the current code." % new_lineno))
        code = self.pycode.co_code
        addr = 0
        line = self.pycode.co_firstlineno
        new_lasti = -1
        offset = 0
        lnotab = self.pycode.co_lnotab
        for offset in xrange(0, len(lnotab), 2):
            addr += ord(lnotab[offset])
            line += ord(lnotab[offset + 1])
            if line >= new_lineno:
                new_lasti = addr
                new_lineno = line
                break

        if new_lasti == -1:
            raise OperationError(space.w_ValueError,
                  space.wrap("line %d comes after the current code." % new_lineno))

        # Don't jump to a line with an except in it.
        if ord(code[new_lasti]) in (DUP_TOP, POP_TOP):
            raise OperationError(space.w_ValueError,
                  space.wrap("can't jump to 'except' line as there's no exception"))
            
        # Don't jump into or out of a finally block.
        f_lasti_setup_addr = -1
        new_lasti_setup_addr = -1
        blockstack = []
        addr = 0
        while addr < len(code):
            op = ord(code[addr])
            if op in (SETUP_LOOP, SETUP_EXCEPT, SETUP_FINALLY):
                blockstack.append([addr, False])
            elif op == POP_BLOCK:
                setup_op = ord(code[blockstack[-1][0]])
                if setup_op == SETUP_FINALLY:
                    blockstack[-1][1] = True
                else:
                    blockstack.pop()
            elif op == END_FINALLY:
                if len(blockstack) > 0:
                    setup_op = ord(code[blockstack[-1][0]])
                    if setup_op == SETUP_FINALLY:
                        blockstack.pop()

            if addr == new_lasti or addr == self.last_instr:
                for ii in range(len(blockstack)):
                    setup_addr, in_finally = blockstack[~ii]
                    if in_finally:
                        if addr == new_lasti:
                            new_lasti_setup_addr = setup_addr
                        if addr == self.last_instr:
                            f_lasti_setup_addr = setup_addr
                        break
                    
            if op >= HAVE_ARGUMENT:
                addr += 3
            else:
                addr += 1
                
        assert len(blockstack) == 0

        if new_lasti_setup_addr != f_lasti_setup_addr:
            raise OperationError(space.w_ValueError,
                  space.wrap("can't jump into or out of a 'finally' block %d -> %d" %
                             (f_lasti_setup_addr, new_lasti_setup_addr)))

        if new_lasti < self.last_instr:
            min_addr = new_lasti
            max_addr = self.last_instr
        else:
            min_addr = self.last_instr
            max_addr = new_lasti

        delta_iblock = min_delta_iblock = 0
        addr = min_addr
        while addr < max_addr:
            op = ord(code[addr])

            if op in (SETUP_LOOP, SETUP_EXCEPT, SETUP_FINALLY):
                delta_iblock += 1
            elif op == POP_BLOCK:
                delta_iblock -= 1
                if delta_iblock < min_delta_iblock:
                    min_delta_iblock = delta_iblock

            if op >= opcode.HAVE_ARGUMENT:
                addr += 3
            else:
                addr += 1

        f_iblock = len(self.blockstack)
        min_iblock = f_iblock + min_delta_iblock
        if new_lasti > self.last_instr:
            new_iblock = f_iblock + delta_iblock
        else:
            new_iblock = f_iblock - delta_iblock

        if new_iblock > min_iblock:
            raise OperationError(space.w_ValueError,
                                 space.wrap("can't jump into the middle of a block"))

        while f_iblock > new_iblock:
            block = self.blockstack.pop()
            block.cleanup(self)
            f_iblock -= 1
            
        self.f_lineno = new_lineno
        self.last_instr = new_lasti
            
    def get_last_lineno(self):
        "Returns the line number of the instruction currently being executed."
        return pytraceback.offset2lineno(self.pycode, self.last_instr)

    def fget_f_builtins(space, self):
        return self.get_builtin().getdict()

    def fget_f_back(space, self):
        return self.space.wrap(self.f_back)

    def fget_f_lasti(space, self):
        return self.space.wrap(self.last_instr)

    def fget_f_trace(space, self):
        return self.w_f_trace

    def fset_f_trace(space, self, w_trace):
        if space.is_w(w_trace, space.w_None):
            self.w_f_trace = None
        else:
            self.w_f_trace = w_trace
            self.f_lineno = self.get_last_lineno()

    def fdel_f_trace(space, self): 
        self.w_f_trace = None 

    def fget_f_exc_type(space, self):
        if self.last_exception is not None:
            f = self.f_back
            while f is not None and f.last_exception is None:
                f = f.f_back
            if f is not None:
                return f.last_exception.w_type
        return space.w_None
         
    def fget_f_exc_value(space, self):
        if self.last_exception is not None:
            f = self.f_back
            while f is not None and f.last_exception is None:
                f = f.f_back
            if f is not None:
                return f.last_exception.w_value
        return space.w_None

    def fget_f_exc_traceback(space, self):
        if self.last_exception is not None:
            f = self.f_back
            while f is not None and f.last_exception is None:
                f = f.f_back
            if f is not None:
                return space.wrap(f.last_exception.application_traceback)
        return space.w_None
         
    def fget_f_restricted(space, self):
        if space.config.objspace.honor__builtins__:
            return space.wrap(self.builtin is not space.builtin)
        return space.w_False

# ____________________________________________________________

def get_block_class(opname):
    # select the appropriate kind of block
    from pypy.interpreter.pyopcode import block_classes
    return block_classes[opname]

def unpickle_block(space, w_tup):
    w_opname, w_handlerposition, w_valuestackdepth = space.unpackiterable(w_tup)
    opname = space.str_w(w_opname)
    handlerposition = space.int_w(w_handlerposition)
    valuestackdepth = space.int_w(w_valuestackdepth)
    assert valuestackdepth >= 0
    blk = instantiate(get_block_class(opname))
    blk.handlerposition = handlerposition
    blk.valuestackdepth = valuestackdepth
    return blk
