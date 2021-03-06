from pypy.objspace.std import StdObjSpace 
from pypy.tool.udir import udir
from pypy.conftest import gettestobjspace
from pypy.tool.autopath import pypydir
from pypy.rpython.module.ll_os import RegisterOs
import os
import py
import sys

def setup_module(mod): 
    mod.space = gettestobjspace(usemodules=['posix'])
    mod.path = udir.join('posixtestfile.txt') 
    mod.path.write("this is a test")
    mod.path2 = udir.join('posixtestlargefile')
    pdir = udir.ensure('posixtestdir', dir=True)
    pdir.join('file1').write("test1")
    os.chmod(str(pdir.join('file1')), 0600)
    pdir.join('file2').write("test2")
    pdir.join('another_longer_file_name').write("test3")
    mod.pdir = pdir

def need_sparse_files():
    if sys.platform == 'darwin':
        py.test.skip("no sparse files on default Mac OS X file system")
    if os.name == 'nt':
        py.test.skip("no sparse files on Windows")

class AppTestPosix: 
    def setup_class(cls): 
        cls.space = space 
        cls.w_posix = space.appexec([], "(): import %s as m ; return m" % os.name)
        cls.w_path = space.wrap(str(path))
        cls.w_path2 = space.wrap(str(path2))
        cls.w_pdir = space.wrap(str(pdir))
        if hasattr(os, 'getuid'):
            cls.w_getuid = space.wrap(os.getuid())
            cls.w_geteuid = space.wrap(os.geteuid())
        if hasattr(os, 'getgid'):
            cls.w_getgid = space.wrap(os.getgid())

    def setup_method(self, meth):
        if getattr(meth, 'need_sparse_files', False):
            need_sparse_files()
    
    def test_posix_is_pypy_s(self): 
        assert self.posix.__file__ 

    def test_some_posix_basic_operation(self): 
        path = self.path 
        posix = self.posix 
        fd = posix.open(path, posix.O_RDONLY, 0777)
        fd2 = posix.dup(fd)
        assert not posix.isatty(fd2) 
        s = posix.read(fd, 1)
        assert s == 't'
        posix.lseek(fd, 5, 0)
        s = posix.read(fd, 1)
        assert s == 'i'
        st = posix.fstat(fd)
        posix.close(fd2)
        posix.close(fd)

        import sys, stat
        assert st[0] == st.st_mode
        assert st[1] == st.st_ino
        assert st[2] == st.st_dev
        assert st[3] == st.st_nlink
        assert st[4] == st.st_uid
        assert st[5] == st.st_gid
        assert st[6] == st.st_size
        assert st[7] == int(st.st_atime)
        assert st[8] == int(st.st_mtime)
        assert st[9] == int(st.st_ctime)

        assert stat.S_IMODE(st.st_mode) & stat.S_IRUSR
        assert stat.S_IMODE(st.st_mode) & stat.S_IWUSR
        if not sys.platform.startswith('win'):
            assert not (stat.S_IMODE(st.st_mode) & stat.S_IXUSR)

        assert st.st_size == 14
        assert st.st_nlink == 1

        #if sys.platform.startswith('linux2'):
        #    # expects non-integer timestamps - it's unlikely that they are
        #    # all three integers
        #    assert ((st.st_atime, st.st_mtime, st.st_ctime) !=
        #            (st[7],       st[8],       st[9]))
        #    assert st.st_blksize * st.st_blocks >= st.st_size
        if sys.platform.startswith('linux2'):
            assert hasattr(st, 'st_rdev')

    def test_pickle(self):
        import pickle, os
        st = self.posix.stat(os.curdir)
        print type(st).__module__
        s = pickle.dumps(st)
        print repr(s)
        new = pickle.loads(s)
        assert new == st
        assert type(new) is type(st)

    def test_open_exception(self): 
        posix = self.posix
        try: 
            posix.open('qowieuqwoeiu', 0, 0)
        except OSError: 
            pass
        else: 
            assert 0

    def test_functions_raise_error(self): 
        def ex(func, *args):
            try:
                func(*args)
            except OSError: 
                pass
            else:
                raise AssertionError("%s(%s) did not raise" %(
                                     func.__name__, 
                                     ", ".join([str(x) for x in args])))
        UNUSEDFD = 123123
        ex(self.posix.open, "qweqwe", 0, 0)
        ex(self.posix.lseek, UNUSEDFD, 123, 0)
        #apparently not posix-required: ex(self.posix.isatty, UNUSEDFD)
        ex(self.posix.read, UNUSEDFD, 123)
        ex(self.posix.write, UNUSEDFD, "x")
        ex(self.posix.close, UNUSEDFD)
        #UMPF cpython raises IOError ex(self.posix.ftruncate, UNUSEDFD, 123)
        ex(self.posix.fstat, UNUSEDFD)
        ex(self.posix.stat, "qweqwehello")
        # how can getcwd() raise? 
        ex(self.posix.dup, UNUSEDFD)

    def test_fdopen(self):
        path = self.path
        posix = self.posix
        fd = posix.open(path, posix.O_RDONLY, 0777)
        f = posix.fdopen(fd, "r")
        f.close()

    def test_listdir(self):
        pdir = self.pdir
        posix = self.posix 
        result = posix.listdir(pdir)
        result.sort()
        assert result == ['another_longer_file_name',
                          'file1',
                          'file2']


    def test_access(self):
        pdir = self.pdir + '/file1'
        posix = self.posix

        assert posix.access(pdir, posix.R_OK)
        assert posix.access(pdir, posix.W_OK)
        import sys
        if sys.platform != "win32":
            assert not posix.access(pdir, posix.X_OK)


    def test_times(self):
        """
        posix.times() should return a five-tuple giving float-representations
        (seconds, effectively) of the four fields from the underlying struct
        tms and the return value.
        """
        result = self.posix.times()
        assert isinstance(result, tuple)
        assert len(result) == 5
        for value in result:
            assert isinstance(value, float)


    def test_strerror(self):
        assert isinstance(self.posix.strerror(0), str)
        assert isinstance(self.posix.strerror(1), str)

    if hasattr(__import__(os.name), "fork"):
        def test_fork(self):
            os = self.posix
            pid = os.fork()
            if pid == 0:   # child
                os._exit(4)
            pid1, status1 = os.waitpid(pid, 0)
            assert pid1 == pid
            # XXX check status1
        pass # <- please, inspect.getsource(), don't crash

    if hasattr(__import__(os.name), "execv"):
        def test_execv(self):
            os = self.posix
            if not hasattr(os, "fork"):
                skip("Need fork() to test execv()")
            pid = os.fork()
            if pid == 0:
                os.execv("/usr/bin/env", ["env", "python", "-c", "open('onefile', 'w').write('1')"])
            os.waitpid(pid, 0)
            assert open("onefile").read() == "1"
            os.unlink("onefile")
        
        def test_execv_raising(self):
            os = self.posix
            raises(OSError, 'os.execv("saddsadsadsadsa", ["saddsadsasaddsa"])')

        def test_execv_raising2(self):
            os = self.posix
            def t(n):
                try:
                    os.execv("xxx", n)
                except TypeError,t:
                    assert t.args[0] == "execv() arg 2 must be an iterable of strings"
                else:
                    py.test.fail("didn't raise")
            t(3)
            t([3, "a"])

        def test_execve(self):
            os = self.posix
            if not hasattr(os, "fork"):
                skip("Need fork() to test execve()")
            pid = os.fork()
            if pid == 0:
                os.execve("/usr/bin/env", ["env", "python", "-c", "import os; open('onefile', 'w').write(os.environ['ddd'])"], {'ddd':'xxx'})
            os.waitpid(pid, 0)
            assert open("onefile").read() == "xxx"
            os.unlink("onefile")
        pass # <- please, inspect.getsource(), don't crash

    if hasattr(__import__(os.name), 'popen'):
        def test_popen(self):
            skip("Not implemented")
            os = self.posix
            stream = os.popen('echo 1')
            assert stream.read() == '1\n'

    if hasattr(__import__(os.name), '_getfullpathname'):
        def test__getfullpathname(self):
            # nt specific
            posix = self.posix
            import os
            sysdrv = os.getenv("SystemDrive", "C:")
            # just see if it does anything
            path = sysdrv + 'hubber'
            assert os.sep in posix._getfullpathname(path)

    def test_utime(self):
        os = self.posix
        import os.path
        # XXX utimes & float support
        path = os.path.join(self.pdir, "test_utime.txt")
        fh = open(path, "w")
        fh.write("x")
        fh.close()
        from time import time, sleep
        t0 = time()
        sleep(1)
        os.utime(path, None)
        assert os.stat(path).st_atime > t0
        os.utime(path, (int(t0), int(t0)))
        assert int(os.stat(path).st_atime) == int(t0)

    def test_utime_raises(self):
        os = self.posix
        raises(TypeError, "os.utime('xxx', 3)")
        raises(OSError, "os.utime('somefilewhichihopewouldneverappearhere', None)")

    for name in RegisterOs.w_star:
        if hasattr(os, name):
            values = [0, 1, 127, 128, 255]
            code = py.code.Source("""
            def test_wstar(self):
                os = self.posix
                %s
            """ % "\n    ".join(["assert os.%s(%d) == %d" % (name, value,
                             getattr(os, name)(value)) for value in values]))
            d = {}
            exec code.compile() in d
            locals()['test_' + name] = d['test_wstar']

    if hasattr(os, 'WIFSIGNALED'):
        def test_wifsignaled(self):
            os = self.posix
            assert os.WIFSIGNALED(0) == False
            assert os.WIFSIGNALED(1) == True

    def test_os_uname(self):
        skip("Uname broken")
        os = self.posix
        res = os.uname()
        assert len(res) == 5
        for i in res:
            assert isinstance(i, str)
        assert isinstance(res, tuple)

    if hasattr(os, 'getuid'):
        def test_os_getuid(self):
            os = self.posix
            assert os.getuid() == self.getuid
            assert os.geteuid() == self.geteuid

    if hasattr(os, 'getgid'):
        def test_os_getgid(self):
            os = self.posix
            assert os.getgid() == self.getgid

    def test_largefile(self):
        os = self.posix
        fd = os.open(self.path2, os.O_RDWR | os.O_CREAT, 0666)
        os.ftruncate(fd, 10000000000L)
        res = os.lseek(fd, 9900000000L, 0)
        assert res == 9900000000L
        res = os.lseek(fd, -5000000000L, 1)
        assert res == 4900000000L
        res = os.lseek(fd, -5200000000L, 2)
        assert res == 4800000000L
        os.close(fd)

        st = os.stat(self.path2)
        assert st.st_size == 10000000000L
    test_largefile.need_sparse_files = True

class AppTestEnvironment(object):
    def setup_class(cls): 
        cls.space = space 
        cls.w_posix = space.appexec([], "(): import %s as m ; return m" % os.name)
        cls.w_os = space.appexec([], "(): import os; return os")
        cls.w_path = space.wrap(str(path))

    def test_environ(self):
        posix = self.posix
        os = self.os
        assert posix.environ['PATH']
        del posix.environ['PATH']
        def fn(): posix.environ['PATH']
        raises(KeyError, fn)

    if hasattr(__import__(os.name), "unsetenv"):
        def test_unsetenv_nonexisting(self):
            os = self.os
            os.unsetenv("XYZABC") #does not raise
            try:
                os.environ["ABCABC"]
            except KeyError:
                pass
            else:
                raise AssertionError("did not raise KeyError")
            os.environ["ABCABC"] = "1"
            assert os.environ["ABCABC"] == "1"
            os.unsetenv("ABCABC")
            cmd = '''python -c "import os, sys; sys.exit(int('ABCABC' in os.environ))" '''
            res = os.system(cmd)
            assert res == 0

    def test_tmpfile(self):
        os = self.os
        f = os.tmpfile()
        f.write("xxx")
        f.flush()
        f.seek(0, 0)
        assert isinstance(f, file)
        assert f.read() == 'xxx'

class TestPexpect(object):
    # XXX replace with AppExpectTest class as soon as possible
    def setup_class(cls):
        try:
            import pexpect
        except ImportError:
            py.test.skip("pexpect not found")
    
    def _spawn(self, *args, **kwds):
        import pexpect
        print 'SPAWN:', args, kwds
        child = pexpect.spawn(*args, **kwds)
        child.logfile = sys.stdout
        return child

    def spawn(self, argv):
        py_py = py.path.local(pypydir).join('bin', 'py.py')
        return self._spawn(sys.executable, [str(py_py)] + argv)

    def test_ttyname(self):
        source = py.code.Source("""
        import os, sys
        assert os.ttyname(sys.stdin.fileno())
        print 'ok!'
        """)
        f = udir.join("test_ttyname.py")
        f.write(source)
        child = self.spawn([str(f)])
        child.expect('ok!')
