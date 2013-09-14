# Copyright 2012 the rootpy developers
# distributed under the terms of the GNU General Public License
from __future__ import absolute_import

from array import array
from itertools import product, izip
import operator
import uuid

import ROOT

from .. import asrootpy, QROOT, log; log = log[__name__]
from ..base import NamedObject, isbasictype
from ..decorators import snake_case_methods
from .base import Plottable, dim
from ..context import invisible_canvas
from .graph import Graph


__all__ = [
    'Hist',
    'Hist1D',
    'Hist2D',
    'Hist3D',
    'HistStack',
    'Efficiency',
    'histogram',
]


class DomainError(Exception):
    pass


class BinProxy(object):

    def __init__(self, hist, idx):
        self.hist = hist
        self.idx = idx
        self.xyz = hist.xyz(idx)

    @property
    def overflow(self):
        """
        Returns true if this BinProxy is for an overflow bin
        """
        indices = self.hist.xyz(self.idx)
        for i in xrange(self.hist.GetDimension()):
            if indices[i] == 0 or indices[i] == self.hist.nbins(i) + 1:
                return True

    @property
    def x(self):
        return self.hist.axis_bininfo(0, self.xyz[0])

    @property
    def y(self):
        return self.hist.axis_bininfo(1, self.xyz[1])

    @property
    def z(self):
        return self.hist.axis_bininfo(2, self.xyz[2])

    @property
    def value(self):
        return self.hist.GetBinContent(self.idx)

    @value.setter
    def value(self, v):
        return self.hist.SetBinContent(self.idx, v)

    @property
    def error(self):
        return self.hist.GetBinError(self.idx)

    @error.setter
    def error(self, e):
        return self.hist.SetBinError(self.idx, e)

    def __imul__(self, v):
        self.value *= v
        self.error *= v

    def __repr__(self):

        return '{0}({1}, {2})'.format(
            self.__class__.__name__, self.hist, self.idx)


class _HistBase(Plottable, NamedObject):

    def xyz(self, i):
        x, y, z = ROOT.Long(0), ROOT.Long(0), ROOT.Long(0)
        self.GetBinXYZ(i, x, y, z)
        return x, y, z

    def axis_bininfo(self, axi, i):
        class bi:
            ax = self.axis(axi)
            lo = ax.GetBinLowEdge(i)
            center = ax.GetBinCenter(i)
            up = ax.GetBinUpEdge(i)
            width = ax.GetBinWidth(i)
        return bi

    def bins(self, overflow=False):
        for i in xrange(self.GetSize()):
            bproxy = BinProxy(self, i)
            if not overflow and bproxy.overflow:
                continue
            yield bproxy

    TYPES = dict((c, [getattr(QROOT, "TH{0}{1}".format(d, c))
                      for d in (1, 2, 3)])
                 for c in "CSIFD")

    def _parse_args(self, args, ignore_extras=False):

        params = [{'bins': None,
                   'nbins': None,
                   'low': None,
                   'high': None} for _ in xrange(dim(self))]

        for param in params:
            if len(args) == 0:
                raise TypeError("Did not receive expected number of arguments")
            if hasattr(args[0], '__iter__'):
                if list(sorted(args[0])) != list(args[0]):
                    raise ValueError(
                        "Bin edges must be sorted in ascending order")
                if len(set(args[0])) != len(args[0]):
                    raise ValueError("Bin edges must not be repeated")
                param['bins'] = args[0]
                param['nbins'] = len(args[0]) - 1
                args = args[1:]
            elif len(args) >= 3:
                nbins = args[0]
                if type(nbins) is not int:
                    raise TypeError(
                        "The number of bins must be an integer")
                if nbins < 1:
                    raise ValueError(
                        "The number of bins must be positive")
                low = args[1]
                if not isbasictype(low):
                    raise TypeError(
                        "The lower bound must be an int, float, or long")
                high = args[2]
                if not isbasictype(high):
                    raise TypeError(
                        "The upper bound must be an int, float, or long")
                param['nbins'] = nbins
                param['low'] = low
                param['high'] = high
                if low >= high:
                    raise ValueError(
                        "Upper bound (you gave {0:f}) "
                        "must be greater than lower "
                        "bound (you gave {1:f})".format(
                            float(low), float(high)))
                args = args[3:]
            else:
                raise TypeError(
                    "Did not receive expected number of arguments")

        if ignore_extras:
            # used by Profile where range of profiled axis may be specified
            return params, args

        if len(args) != 0:
            raise TypeError(
                "Did not receive expected number of arguments")

        return params

    @classmethod
    def divide(cls, h1, h2, c1=1., c2=1., option=''):

        ratio = h1.Clone()
        ROOT.TH1.Divide(ratio, h1, h2, c1, c2, option)
        return ratio

    def Fill(self, *args):

        bin = super(_HistBase, self).Fill(*args)
        if bin > 0:
            return bin - 1
        return bin

    def nbins(self, axis=0):

        if axis == 0:
            return self.GetNbinsX()
        elif axis == 1:
            return self.GetNbinsY()
        elif axis == 2:
            return self.GetNbinsZ()
        else:
            raise ValueError("axis must be 0, 1, or 2")

    @property
    def axes(self):
        return [self.axis(i) for i in xrange(self.GetDimension())]

    def axis(self, axis=0):

        if axis == 0:
            return self.GetXaxis()
        elif axis == 1:
            return self.GetYaxis()
        elif axis == 2:
            return self.GetZaxis()
        else:
            raise ValueError("axis must be 0, 1, or 2")

    def __len__(self):
        """
        The total number of bins, including overflow bins
        """
        return self.GetSize()

    def __iter__(self):
        """
        Iterate over the bin proxies
        """
        return self.bins(overflow=True)

    def _range_check(self, index, axis=None):

        if axis is None:
            if not 0 <= index < self.GetSize():
                raise IndexError(
                    "global bin index {0:d} is out of range".format(index))
        elif not 0 <= index < self.nbins(axis=axis) + 2:
            raise IndexError(
                "bin index {0:d} along axis {1:d} is out of range".format(
                    index, axis))

    def __getitem__(self, index):
        """
        Return a BinProxy or list of BinProxies if index is a slice.
        """
        if isinstance(index, slice):
            return list(self)[index]
        if isinstance(index, tuple):
            ix, iy, iz = 0, 0, 0
            ndim = self.GetDimension()
            if ndim == 2:
                ix, iy = index
                self._range_check(ix, axis=0)
                self._range_check(iy, axis=1)
            elif ndim == 3:
                ix, iy, iz = index
                self._range_check(ix, axis=0)
                self._range_check(iy, axis=1)
                self._range_check(iz, axis=2)
            index = self.GetBin(ix, iy, iz)
        else:
            self._range_check(index)
        return BinProxy(self, index)

    def __setitem__(self, index, value):
        """
        Set bin contents and additionally bin errors if value is a BinProxy or
        a 2-tuple containing the value and error.
        If index is a slice then value must be a list of values, BinProxies, or
        2-tuples of the same length as the slice.
        """
        if isinstance(index, slice):
            # TODO: support slicing along axes separately
            indices = range(*index.indices(self.GetSize()))

            if len(indices) != len(value):
                raise RuntimeError(
                    "len(value) != len(indices) ({0} != {1})".format(
                        len(value), len(indices)))

            if value and isinstance(value[0], BinProxy):
                for i, v in izip(indices, value):
                    self.SetBinContent(i, v.value)
                    self.SetBinError(i, v.error)
            elif value and isinstance(value[0], tuple):
                for i, v in izip(indices, value):
                    _value, _error = value
                    self.SetBinContent(i, _value)
                    self.SetBinError(i, _error)
            else:
                for i, v in izip(indices, value):
                    self.SetBinContent(i, v)
            return

        if isinstance(index, tuple):
            ix, iy, iz = 0, 0, 0
            ndim = self.GetDimension()
            if ndim == 2:
                ix, iy = index
                self._range_check(ix, axis=0)
                self._range_check(iy, axis=1)
            elif ndim == 3:
                ix, iy, iz = index
                self._range_check(ix, axis=0)
                self._range_check(iy, axis=1)
                self._range_check(iz, axis=2)
            index = self.GetBin(ix, iy, iz)
        else:
            self._range_check(index)

        if isinstance(value, BinProxy):
            self.SetBinContent(index, value.value)
            self.SetBinError(index, value.error)
        elif isinstance(value, tuple):
            value, error = value
            self.SetBinContent(index, value)
            self.SetBinError(index, error)
        else:
            self.SetBinContent(index, value)

    def uniform(self, axis=None, precision=1E-7):
        """
        Return True if the binning is uniform along the specified axis.
        If axis is None (the default), then return True if the binning is
        uniform along all axes. Otherwise return False.

        Parameters
        ----------

        axis : int (default=None)
            Axis along which to check if the binning is uniform. If None,
            then check all axes.

        precision : float (default=1E-7)
            The threshold below which differences in bin widths are ignored and
            treated as equal.

        Returns
        -------

        True if the binning is uniform, otherwise False.

        """
        if axis is None:
            for axis in xrange(self.GetDimension()):
                widths = list(self._width(axis=axis))
                if not all(abs(x - widths[0]) < precision for x in widths):
                    return False
            return True
        widths = list(self._width(axis=axis))
        return all(abs(x - widths[0]) < precision for x in widths)

    def underflow(self, axis=0):
        """
        Return the underflow for the given axis.

        Depending on the dimension of the histogram, may return an array.
        """
        if axis not in range(3):
            raise ValueError("axis must be 0, 1, or 2")
        if self.DIM == 1:
            return self.GetBinContent(0)
        elif self.DIM == 2:
            def idx(i):
                arg = [i]
                arg.insert(axis, 0)
                return arg
            return [
                self.GetBinContent(*idx(i))
                for i in xrange(self.nbins((axis + 1) % 2) + 2)]
        elif self.DIM == 3:
            axes = range(3)
            axes.remove(axis)
            axis2, axis3 = axes
            def idx(i, j):
                arg = [i, j]
                arg.insert(axis, 0)
                return arg
            return [[
                self.GetBinContent(*idx(i, j))
                for i in xrange(self.nbins(axis2) + 2)]
                for j in xrange(self.nbins(axis3) + 2)]

    def overflow(self, axis=0):
        """
        Return the overflow for the given axis.

        Depending on the dimension of the histogram, may return an array.
        """
        if axis not in range(3):
            raise ValueError("axis must be 0, 1, or 2")
        if self.DIM == 1:
            return self.GetBinContent(self.nbins(0) + 1)
        elif self.DIM == 2:
            axes = range(2)
            axes.remove(axis)
            axis2 = axes[0]
            nbins_axis = self.nbins(axis)
            def idx(i):
                arg = [i]
                arg.insert(axis, nbins_axis + 1)
                return arg
            return [
                self.GetBinContent(*idx(i))
                for i in xrange(self.nbins(axis2) + 2)]
        elif self.DIM == 3:
            axes = range(3)
            axes.remove(axis)
            axis2, axis3 = axes
            nbins_axis = self.nbins(axis)
            def idx(i, j):
                arg = [i, j]
                arg.insert(axis, nbins_axis + 1)
                return arg
            return [[
                self.GetBinContent(*idx(i, j))
                for i in xrange(self.nbins(axis2) + 2)]
                for j in xrange(self.nbins(axis3) + 2)]

    def lowerbound(self, axis=0):

        if axis == 0:
            return self.xedges(0)
        if axis == 1:
            return self.yedges(0)
        if axis == 2:
            return self.zedges(0)
        return ValueError("axis must be 0, 1, or 2")

    def upperbound(self, axis=0):

        if axis == 0:
            return self.xedges(-1)
        if axis == 1:
            return self.yedges(-1)
        if axis == 2:
            return self.zedges(-1)
        return ValueError("axis must be 0, 1, or 2")

    def _centers(self, axis, index=None, overflow=False):

        nbins = self.nbins(axis)
        ax = self.axis(axis)
        if index is None:
            def temp_generator():
                if overflow:
                    yield float('-inf')
                for index in xrange(1, nbins + 1):
                    yield ax.GetBinCenter(index)
                if overflow:
                    yield float('+inf')
            return temp_generator()
        index = index % (nbins + 2)
        if index == 0:
            return float('-inf')
        elif index == nbins + 1:
            return float('+inf')
        return ax.GetBinCenter(index)

    def _edgesl(self, axis, index=None, overflow=False):

        nbins = self.nbins(axis)
        ax = self.axis(axis)
        if index is None:
            def temp_generator():
                if overflow:
                    yield float('-inf')
                for index in xrange(1, nbins + 1):
                    yield ax.GetBinLowEdge(index)
                if overflow:
                    yield ax.GetBinUpEdge(index)
            return temp_generator()
        index = index % (nbins + 2)
        if index == 0:
            return float('-inf')
        if index == nbins + 1:
            return ax.GetBinUpEdge(index)
        return ax.GetBinLowEdge(index)

    def _edgesh(self, axis, index=None, overflow=False):

        nbins = self.nbins(axis)
        ax = self.axis(axis)
        if index is None:
            def temp_generator():
                if overflow:
                    yield ax.GetBinUpEdge(0)
                for index in xrange(1, nbins + 1):
                    yield ax.GetBinUpEdge(index)
                if overflow:
                    yield float('+inf')
            return temp_generator()
        index = index % (nbins + 2)
        if index == 0:
            return ax.GetBinLowEdge(index)
        if index == nbins + 1:
            return float('+inf')
        return ax.GetBinUpEdge(index)

    def _edges(self, axis, index=None, overflow=False):

        nbins = self.nbins(axis)
        ax = self.axis(axis)
        if index is None:
            def temp_generator():
                if overflow:
                    yield float('-inf')
                for index in xrange(1, nbins + 1):
                    yield ax.GetBinLowEdge(index)
                yield ax.GetBinUpEdge(nbins)
                if overflow:
                    yield float('+inf')
            return temp_generator()
        index = index % (nbins + 3)
        if index == 0:
            return float('-inf')
        if index == nbins + 2:
            return float('+inf')
        if index == nbins:
            return ax.GetBinUpEdge(index)
        return ax.GetBinLowEdge(index)

    def _width(self, axis, index=None, overflow=False):

        nbins = self.nbins(axis)
        ax = self.axis(axis)
        if index is None:
            def temp_generator():
                if overflow:
                    yield float('+inf')
                for index in xrange(1, nbins + 1):
                    yield ax.GetBinWidth(index)
                if overflow:
                    yield float('+inf')
            return temp_generator()
        index = index % (nbins + 2)
        if index in (0, nbins + 1):
            return float('+inf')
        return ax.GetBinWidth(index)

    def _erravg(self, axis, index=None, overflow=False):

        nbins = self.nbins(axis)
        ax = self.axis(axis)
        if index is None:
            def temp_generator():
                if overflow:
                    yield float('+inf')
                for index in xrange(1, nbins + 1):
                    yield ax.GetBinWidth(index) / 2.
                if overflow:
                    yield float('+inf')
            return temp_generator()
        index = index % (nbins + 2)
        if index in (0, nbins + 1):
            return float('+inf')
        return ax.GetBinWidth(index) / 2.

    def _err(self, axis, index=None, overflow=False):

        nbins = self.nbins(axis)
        ax = self.axis(axis)
        if index is None:
            def temp_generator():
                if overflow:
                    yield (float('+inf'), float('+inf'))
                for index in xrange(1, nbins + 1):
                    w = ax.GetBinWidth(index) / 2.
                    yield (w, w)
                if overflow:
                    yield (float('+inf'), float('+inf'))
            return temp_generator()
        index = index % (nbins + 2)
        if index in (0, nbins + 1):
            return (float('+inf'), float('+inf'))
        w = ax.GetBinWidth(index) / 2.
        return (w, w)

    def __add__(self, other):

        copy = self.Clone()
        copy += other
        return copy

    def __iadd__(self, other):

        if isbasictype(other):
            if not isinstance(self, _Hist):
                raise ValueError(
                    "A multidimensional histogram must be filled with a tuple")
            self.Fill(other)
        elif type(other) in [list, tuple]:
            if dim(self) not in [len(other), len(other) - 1]:
                raise ValueError(
                    "Dimension of {0} does not match dimension "
                    "of histogram (with optional weight "
                    "as last element)".format(str(other)))
            self.Fill(*other)
        else:
            self.Add(other)
        return self

    def __sub__(self, other):

        copy = self.Clone()
        copy -= other
        return copy

    def __isub__(self, other):

        if isbasictype(other):
            if not isinstance(self, _Hist):
                raise ValueError(
                    "A multidimensional histogram must "
                    "be filled with a tuple")
            self.Fill(other, -1)
        elif type(other) in [list, tuple]:
            if len(other) == dim(self):
                self.Fill(*(other + (-1, )))
            elif len(other) == dim(self) + 1:
                # negate last element
                self.Fill(*(other[:-1] + (-1 * other[-1], )))
            else:
                raise ValueError(
                    "Dimension of {0} does not match dimension "
                    "of histogram (with optional weight "
                    "as last element)".format(str(other)))
        else:
            self.Add(other, -1.)
        return self

    def __mul__(self, other):

        copy = self.Clone()
        copy *= other
        return copy

    def __imul__(self, other):

        if isbasictype(other):
            self.Scale(other)
            return self
        self.Multiply(other)
        return self

    def __div__(self, other):

        copy = self.Clone()
        copy /= other
        return copy

    def __idiv__(self, other):

        if isbasictype(other):
            if other == 0:
                raise ZeroDivisionError()
            self.Scale(1. / other)
            return self
        self.Divide(other)
        return self

    def __radd__(self, other):

        if other == 0:
            return self.Clone()
        raise TypeError(
            "unsupported operand type(s) for +: '{0}' and '{1}'".format(
                other.__class__.__name__, self.__class__.__name__))

    def __rsub__(self, other):

        if other == 0:
            return self.Clone()
        raise TypeError(
            "unsupported operand type(s) for -: '{0}' and '{1}'".format(
                other.__class__.__name__, self.__class__.__name__))

    def __cmp__(self, other):

        diff = self.max() - other.max()
        if diff > 0:
            return 1
        if diff < 0:
            return -1
        return 0

    def fill_array(self, array, weights=None):
        """
        Fill this histogram with a NumPy array
        """
        try:
            from root_numpy import fill_array
        except ImportError:
            log.critical(
                "root_numpy is needed for Hist*.fill_array. "
                "Is it installed and importable?")
            raise
        fill_array(self, array, weights=weights)

    def FillRandom(self, func, ntimes=5000):

        if isinstance(func, QROOT.TF1):
            func = func.GetName()
        super(_HistBase, self).FillRandom(func, ntimes)

    def get_sum_w2(self, x, y=0, z=0):
        """
        Obtain the true number of entries in the bin weighted by w^2
        """
        if self.GetSumw2N() == 0:
            raise RuntimeError(
                "Attempting to access Sumw2 in histogram "
                "where weights were not stored")
        xl = self.GetNbinsX() + 2
        yl = self.GetNbinsY() + 2
        zl = self.GetNbinsZ() + 2
        assert x >= 0 and x < xl
        assert y >= 0 and y < yl
        assert z >= 0 and z < zl
        if self.GetDimension() < 3:
            z = 0
        if self.GetDimension() < 2:
            y = 0
        return self.GetSumw2().At(xl * yl * z + xl * y + x)

    def set_sum_w2(self, w, x, y=0, z=0):
        """
        Sets the true number of entries in the bin weighted by w^2
        """
        if self.GetSumw2N() == 0:
            raise RuntimeError(
                "Attempting to access Sumw2 in histogram "
                "where weights were not stored")
        xl = self.GetNbinsX() + 2
        yl = self.GetNbinsY() + 2
        zl = self.GetNbinsZ() + 2
        assert x >= 0 and x < xl
        assert y >= 0 and y < yl
        assert z >= 0 and z < zl
        if self.GetDimension() < 3:
            z = 0
        if self.GetDimension() < 2:
            y = 0
        self.GetSumw2().SetAt(w, xl * yl * z + xl * y + x)

    def merge_bins(self, bin_ranges, axis=0):
        """
        Merge bins in bin ranges

        Parameters
        ----------

        bin_ranges : list of tuples
            A list of tuples of bin indices for each bin range to be merged
            into one bin.

        axis : int (default=1)
            The integer identifying the axis to merge bins along.

        Returns
        -------

        hist : TH1
            The rebinned histogram.

        Examples
        --------

        Merge the overflow bins into the first and last real bins::

            newhist = hist.merge_bins([(0, 1), (-2, -1)])

        """
        ndim = self.GetDimension()
        if axis > ndim - 1:
            raise ValueError(
                "axis is out of range")
        axis_bins = self.nbins(axis) + 2

        # collect the indices along this axis to be merged
        # support negative indices via slicing
        windows = []
        for window in bin_ranges:
            if len(window) != 2:
                raise ValueError(
                    "bin range tuples must contain two elements")
            l, r = window
            if l == r:
                raise ValueError(
                    "bin indices must not be equal in a merging window")
            if l < 0 and r >= 0:
                raise ValueError(
                    "invalid bin range")
            if r == -1:
                r = axis_bins
            else:
                r += 1
            bin_idx = range(*slice(l, r).indices(axis_bins))
            if bin_idx: # skip []
                windows.append(bin_idx)

        if not windows:
            # no merging will take place so return a clone
            return self.Clone()

        # check that windows do not overlap
        if len(windows) > 1:
            full_list = reduce(operator.add, windows)
            if len(full_list) != len(set(full_list)):
                raise ValueError("bin index windows overlap")

        # construct a mapping from old to new bin index along this axis
        windows.sort()
        mapping = {}
        left_idx = {}
        offset = 0
        for window in windows:
            # put underflow in first bin
            new_idx = window[0] - offset or 1
            left_idx[window[0] or 1] = None
            for idx in window:
                mapping[idx] = new_idx
            offset += len(window) - 1
            if window[0] == 0:
                offset -= 1

        new_axis_bins = axis_bins - offset

        # construct new bin edges
        new_edges = []
        for i, edge in enumerate(self._edges(axis)):
            if (i != axis_bins - 2 and i + 1 in mapping
                and i + 1 not in left_idx):
                continue
            new_edges.append(edge)

        # construct new histogram and fill
        new_hist = self.new_binning_template(new_edges, axis=axis)

        this_axis = self.axis(axis)
        new_axis = new_hist.axis(axis)

        def translate(idx):
            if idx in mapping:
                return mapping[idx]
            if idx == 0:
                return 0
            # use TH1.FindBin to determine where the bins should be merged
            return new_axis.FindBin(this_axis.GetBinCenter(idx))

        for bin in self.bins(overflow=True):
            xyz = bin.xyz
            new_xyz = list(xyz)
            new_xyz[axis] = translate(int(xyz[axis]))

            x, y, z = new_xyz

            new_v = new_hist.GetBinContent(x, y, z)
            new_hist.SetBinContent(x, y, z, new_v + bin.value)

            sum_w2 = self.get_sum_w2(*xyz)
            new_sum_w2 = new_hist.get_sum_w2(x, y, z)
            new_hist.set_sum_w2(sum_w2 + new_sum_w2, x, y, z)

        # transfer stats info
        stat_array = array('d', [0.] * 10)
        self.GetStats(stat_array)
        new_hist.PutStats(stat_array)
        entries = self.GetEntries()
        new_hist.SetEntries(entries)
        return new_hist

    def rebinned(self, bins, axis=0):
        """
        Return a new rebinned histogram

        Parameters
        ----------

        bins : int, tuple, or iterable
            If ``bins`` is an int, then return a histogram that is rebinned by
            grouping N=``bins`` bins together along the axis ``axis``.
            If ``bins`` is a tuple, then it must contain the same number of
            elements as there are dimensions of this histogram and each element
            will be used to rebin along the associated axis.
            If ``bins`` is another iterable, then it will define the bin
            edges along the axis ``axis`` in the new rebinned histogram.

        axis : int, optional (default=0)
            The axis to rebin along.

        Returns
        -------

        The rebinned histogram

        """
        ndim = self.GetDimension()
        if axis >= ndim:
            raise ValueError(
                "axis must be less than the dimensionality of the histogram")

        if isinstance(bins, int):
            _bins = [1] * ndim
            try:
                _bins[axis] = bins
            except IndexError:
                raise ValueError("axis must be 0, 1, or 2")
            bins = tuple(_bins)

        if isinstance(bins, tuple):
            if len(bins) != ndim:
                raise ValueError(
                    "bins must be a tuple with the same "
                    "number of elements as histogram axes")
            newname = uuid.uuid4().hex
            if ndim == 1:
                hist = self.Rebin(bins[0], newname)
            elif ndim == 2:
                hist = self.Rebin2D(bins[0], bins[1], newname)
            else:
                hist = self.Rebin3D(bins[0], bins[1], bins[2], newname)
            hist = asrootpy(hist)
        elif hasattr(bins, '__iter__'):
            hist = self.new_binning_template(bins, axis=axis)
            nbinsx = self.nbins(0)
            nbinsy = self.nbins(1)
            nbinsz = self.nbins(2)
            xaxis = self.xaxis
            yaxis = self.yaxis
            zaxis = self.zaxis
            sum_w2 = self.GetSumw2()
            _sum_w2_at = sum_w2.At
            new_sum_w2 = hist.GetSumw2()
            _new_sum_w2_at = new_sum_w2.At
            _new_sum_w2_setat = new_sum_w2.SetAt
            _x_center = xaxis.GetBinCenter
            _y_center = yaxis.GetBinCenter
            _z_center = zaxis.GetBinCenter
            _find = hist.FindBin
            _set = hist.SetBinContent
            _get = hist.GetBinContent
            _this_get = self.GetBinContent
            _get_bin = super(_HistBase, self).GetBin
            for z in xrange(1, nbinsz + 1):
                for y in xrange(1, nbinsy + 1):
                    for x in xrange(1, nbinsx + 1):
                        newbin = _find(
                            _x_center(x), _y_center(y), _z_center(z))
                        idx = _get_bin(x, y, z)
                        _set(newbin, _get(newbin) + _this_get(idx))
                        _new_sum_w2_setat(
                            _new_sum_w2_at(newbin) + _sum_w2_at(idx),
                            newbin)
            hist.SetEntries(self.GetEntries())
        else:
            raise TypeError(
                "bins must either be an integer, a tuple, or an iterable")
        return hist

    def smoothed(self, iterations=1):
        """
        Return a smoothed copy of this histogram

        Parameters
        ----------

        iterations : int, optional (default=1)
            The number of smoothing iterations

        Returns
        -------

        hist : asrootpy'd histogram
            The smoothed histogram

        """
        copy = self.Clone(shallow=True)
        copy.Smooth(iterations)
        return copy

    def new_binning_template(self, binning, axis=0):
        """
        Return a new empty histogram with the binning modified along the
        specified axis
        """
        ndim = self.GetDimension()
        if axis > ndim - 1:
            raise ValueError(
                "axis is out of range")
        if hasattr(binning, '__iter__'):
            binning = (binning,)
        cls = [Hist, Hist2D, Hist3D][ndim - 1]
        args = []
        for iaxis in xrange(ndim):
            if iaxis == axis:
                args.extend(binning)
            else:
                args.append(list(self._edges(axis=iaxis)))
        return cls(*args, type=self.TYPE)

    def quantiles(self, quantiles,
                  axis=0, strict=False,
                  recompute_integral=False):
        """
        Calculate the quantiles of this histogram.

        Parameters
        ----------

        quantiles : list or int
            A list of cumulative probabilities or an integer used to determine
            equally spaced values between 0 and 1 (inclusive).

        axis : int, optional (default=0)
            The axis to compute the quantiles along. 2D and 3D histograms are
            first projected along the desired axis before computing the
            quantiles.

        strict : bool, optional (default=False)
            If True, then return the sorted unique quantiles corresponding
            exactly to bin edges of this histogram.

        recompute_integral : bool, optional (default=False)
            If this histogram was filled with SetBinContent instead of Fill,
            then the integral must be computed before calculating the
            quantiles.

        Returns
        -------

        output : list or numpy array
            If NumPy is importable then an array of the quantiles is returned,
            otherwise a list is returned.

        """
        if axis >= self.GetDimension():
            raise ValueError(
                "axis must be less than the dimensionality of the histogram")
        if recompute_integral:
            self.ComputeIntegral()
        if isinstance(self, _Hist2D):
            newname = uuid.uuid4().hex
            if axis == 0:
                proj = self.ProjectionX(newname, 1, self.nbins(1))
            elif axis == 1:
                proj = self.ProjectionY(newname, 1, self.nbins(0))
            else:
                raise ValueError("axis must be 0 or 1")
            return asrootpy(proj).quantiles(
                quantiles, strict=strict, recompute_integral=False)
        elif isinstance(self, _Hist3D):
            newname = uuid.uuid4().hex
            if axis == 0:
                proj = self.ProjectionX(
                    newname, 1, self.nbins(1), 1, self.nbins(2))
            elif axis == 1:
                proj = self.ProjectionY(
                    newname, 1, self.nbins(0), 1, self.nbins(2))
            elif axis == 2:
                proj = self.ProjectionZ(
                    newname, 1, self.nbins(0), 1, self.nbins(1))
            else:
                raise ValueError("axis must be 0, 1, or 2")
            return asrootpy(proj).quantiles(
                quantiles, strict=strict, recompute_integral=False)
        try:
            import numpy as np
        except ImportError:
            # use python implementation
            use_numpy = False
        else:
            use_numpy = True
        if isinstance(quantiles, int):
            num_quantiles = quantiles
            if use_numpy:
                qs = np.linspace(0, 1, num_quantiles)
                output = np.empty(num_quantiles, dtype=float)
            else:
                def linspace(start, stop, n):
                    if n == 1:
                        yield start
                        return
                    h = float(stop - start) / (n - 1)
                    for i in range(n):
                        yield start + h * i
                quantiles = list(linspace(0, 1, num_quantiles))
                qs = array('d', quantiles)
                output = array('d', [0.] * num_quantiles)
        else:
            num_quantiles = len(quantiles)
            if use_numpy:
                qs = np.array(quantiles, dtype=float)
                output = np.empty(num_quantiles, dtype=float)
            else:
                qs = array('d', quantiles)
                output = array('d', [0.] * num_quantiles)
        if strict:
            integral = self.GetIntegral()
            nbins = self.nbins(0)
            if use_numpy:
                edges = np.empty(nbins + 1, dtype=float)
                self.GetLowEdge(edges)
                edges[-1] = edges[-2] + self.GetBinWidth(nbins)
                integral = np.ndarray((nbins + 1,), dtype=float, buffer=integral)
                idx = np.searchsorted(integral, qs, side='left')
                output = np.unique(np.take(edges, idx))
            else:
                quantiles = list(set(qs))
                quantiles.sort()
                output = []
                ibin = 0
                for quant in quantiles:
                    # find first bin greater than or equal to quant
                    while integral[ibin] < quant and ibin < nbins + 1:
                        ibin += 1
                    edge = self.GetBinLowEdge(ibin + 1)
                    output.append(edge)
                    if ibin >= nbins + 1:
                        break
                output = list(set(output))
                output.sort()
            return output
        self.GetQuantiles(num_quantiles, output, qs)
        if use_numpy:
            return output
        return list(output)

    def max(self, include_error=False):

        if not include_error:
            return self.GetBinContent(self.GetMaximumBin())
        clone = self.Clone(shallow=True)
        for i in xrange(self.GetSize()):
            clone.SetBinContent(
                i, clone.GetBinContent(i) + clone.GetBinError(i))
        return clone.GetBinContent(clone.GetMaximumBin())

    def min(self, include_error=False):

        if not include_error:
            return self.GetBinContent(self.GetMinimumBin())
        clone = self.Clone(shallow=True)
        for i in xrange(self.GetSize()):
            clone.SetBinContent(
                i, clone.GetBinContent(i) - clone.GetBinError(i))
        return clone.GetBinContent(clone.GetMinimumBin())


class _Hist(_HistBase):

    DIM = 1

    def x(self, index=None, overflow=False):

        return self._centers(0, index, overflow=overflow)

    def xerravg(self, index=None, overflow=False):

        return self._erravg(0, index, overflow=overflow)

    def xerrl(self, index=None, overflow=False):

        return self._erravg(0, index, overflow=overflow)

    def xerrh(self, index=None, overflow=False):

        return self._erravg(0, index, overflow=overflow)

    def xerr(self, index=None, overflow=False):

        return self._err(0, index, overflow=overflow)

    def xwidth(self, index=None, overflow=False):

        return self._width(0, index, overflow=overflow)

    def xedgesl(self, index=None, overflow=False):

        return self._edgesl(0, index, overflow=overflow)

    def xedgesh(self, index=None, overflow=False):

        return self._edgesh(0, index, overflow=overflow)

    def xedges(self, index=None, overflow=False):

        return self._edges(0, index, overflow=overflow)

    def yerrh(self, index=None, overflow=False):

        return self.yerravg(index, overflow=overflow)

    def yerrl(self, index=None, overflow=False):

        return self.yerravg(index, overflow=overflow)

    def y(self, index=None, overflow=False):

        nbins = self.nbins(0)
        if index is None:
            if overflow:
                start = 0
                end_offset = 2
            else:
                start = 1
                end_offset = 1
            return (self.GetBinContent(i)
                    for i in xrange(start, nbins + end_offset))
        index = index % (nbins + 2)
        return self.GetBinContent(index)

    def yerravg(self, index=None, overflow=False):

        nbins = self.nbins(0)
        if index is None:
            if overflow:
                start = 0
                end_offset = 2
            else:
                start = 1
                end_offset = 1
            return (self.GetBinError(i)
                    for i in xrange(start, nbins + end_offset))
        index = index % (nbins + 2)
        return self.GetBinError(index)

    def yerr(self, index=None, overflow=False):

        nbins = self.nbins(0)
        if index is None:
            if overflow:
                start = 0
                end_offset = 2
            else:
                start = 1
                end_offset = 1
            return ((self.yerrl(i), self.yerrh(i))
                    for i in xrange(start, nbins + end_offset))
        index = index % (nbins + 2)
        return (self.yerrl(index), self.yerrh(index))

    def expectation(self, startbin=1, endbin=None):

        if endbin is not None and endbin < startbin:
            raise DomainError("``endbin`` should be greated than ``startbin``")
        if endbin is None:
            endbin = self.nbins(0)
        expect = 0.
        norm = 0.
        for index in xrange(startbin, endbin + 1):
            val = self[index]
            expect += val * self.x(index)
            norm += val
        if norm > 0:
            return expect / norm
        else:
            return (self.xedges(endbin + 1) + self.xedges(startbin)) / 2

    def quantiles(self, quantiles, strict=False, recompute_integral=False):
        """
        Calculate the quantiles of this histogram

        Parameters
        ----------

        quantiles : list or int
            A list of cumulative probabilities or an integer used to determine
            equally spaced values between 0 and 1 (inclusive).

        strict : bool, optional (default=False)
            If True, then return the sorted unique quantiles corresponding
            exactly to bin edges of this histogram.

        recompute_integral : bool, optional (default=False)
            If this histogram was filled with SetBinContent instead of Fill,
            then the integral must be computed before calculating the
            quantiles.

        Returns
        -------

        output : list or numpy array
            If NumPy is importable then an array of the quantiles is returned,
            otherwise a list is returned.

        """
        if recompute_integral:
            self.ComputeIntegral()
        try:
            import numpy as np
        except ImportError:
            # use python implementation
            use_numpy = False
        else:
            use_numpy = True
        if isinstance(quantiles, int):
            num_quantiles = quantiles
            if use_numpy:
                qs = np.linspace(0, 1, num_quantiles)
                output = np.empty(num_quantiles, dtype=float)
            else:
                def linspace(start, stop, n):
                    if n == 1:
                        yield start
                        return
                    h = float(stop - start) / (n - 1)
                    for i in range(n):
                        yield start + h * i
                quantiles = list(linspace(0, 1, num_quantiles))
                qs = array('d', quantiles)
                output = array('d', [0.] * num_quantiles)
        else:
            num_quantiles = len(quantiles)
            if use_numpy:
                qs = np.array(quantiles, dtype=float)
                output = np.empty(num_quantiles, dtype=float)
            else:
                qs = array('d', quantiles)
                output = array('d', [0.] * num_quantiles)
        if strict:
            integral = self.GetIntegral()
            nbins = self.nbins(0)
            if use_numpy:
                edges = np.empty(nbins + 1, dtype=float)
                self.GetLowEdge(edges)
                edges[-1] = edges[-2] + self.GetBinWidth(nbins)
                integral = np.ndarray((nbins + 1,), dtype=float, buffer=integral)
                idx = np.searchsorted(integral, qs, side='left')
                output = np.unique(np.take(edges, idx))
            else:
                quantiles = list(set(qs))
                quantiles.sort()
                output = []
                ibin = 0
                for quant in quantiles:
                    # find first bin greater than or equal to quant
                    while integral[ibin] < quant and ibin < nbins + 1:
                        ibin += 1
                    edge = self.GetBinLowEdge(ibin + 1)
                    output.append(edge)
                    if ibin >= nbins + 1:
                        break
                output = list(set(output))
                output.sort()
            return output
        self.GetQuantiles(num_quantiles, output, qs)
        if use_numpy:
            return output
        return list(output)


class _Hist2D(_HistBase):

    DIM = 2

    def x(self, index=None, overflow=False):

        return self._centers(0, index, overflow=overflow)

    def xerravg(self, index=None, overflow=False):

        return self._erravg(0, index, overflow=overflow)

    def xerrl(self, index=None, overflow=False):

        return self._erravg(0, index, overflow=overflow)

    def xerrh(self, index=None, overflow=False):

        return self._erravg(0, index, overflow=overflow)

    def xerr(self, index=None, overflow=False):

        return self._err(0, index, overflow=overflow)

    def xwidth(self, index=None, overflow=False):

        return self._width(0, index, overflow=overflow)

    def xedgesl(self, index=None, overflow=False):

        return self._edgesl(0, index, overflow=overflow)

    def xedgesh(self, index=None, overflow=False):

        return self._edgesh(0, index, overflow=overflow)

    def xedges(self, index=None, overflow=False):

        return self._edges(0, index, overflow=overflow)

    def y(self, index=None, overflow=False):

        return self._centers(1, index, overflow=overflow)

    def yerravg(self, index=None, overflow=False):

        return self._erravg(1, index, overflow=overflow)

    def yerrl(self, index=None, overflow=False):

        return self._erravg(1, index, overflow=overflow)

    def yerrh(self, index=None, overflow=False):

        return self._erravg(1, index, overflow=overflow)

    def yerr(self, index=None, overflow=False):

        return self._err(1, index, overflow=overflow)

    def ywidth(self, index=None, overflow=False):

        return self._width(1, index, overflow=overflow)

    def yedgesl(self, index=None, overflow=False):

        return self._edgesl(1, index, overflow=overflow)

    def yedgesh(self, index=None, overflow=False):

        return self._edgesh(1, index, overflow=overflow)

    def yedges(self, index=None, overflow=False):

        return self._edges(1, index, overflow=overflow)

    def zerrh(self, index=None, overflow=False):

        return self.zerravg(index, overflow=overflow)

    def zerrl(self, index=None, overflow=False):

        return self.zerravg(index, overflow=overflow)

    def z(self, ix=None, iy=None, overflow=False):

        if ix is None and iy is None:
            if overflow:
                start = 0
                end_offest = 2
            else:
                start = 1
                end_offset = 1
            return [[self.GetBinContent(ix, iy)
                    for iy in xrange(start, self.nbins(1) + end_offset)]
                    for ix in xrange(start, self.nbins(0) + end_offset)]
        ix = ix % (self.nbins(0) + 2)
        iy = iy % (self.nbins(1) + 2)
        return self.GetBinContent(ix, iy)

    def zerravg(self, ix=None, iy=None, overflow=False):

        if ix is None and iy is None:
            if overflow:
                start = 0
                end_offest = 2
            else:
                start = 1
                end_offset = 1
            return [[self.GetBinError(ix, iy)
                    for iy in xrange(start, self.nbins(1) + end_offset)]
                    for ix in xrange(start, self.nbins(0) + end_offset)]
        ix = ix % (self.nbins(0) + 2)
        iy = iy % (self.nbins(1) + 2)
        return self.GetBinError(ix, iy)

    def zerr(self, ix=None, iy=None, overflow=False):

        if ix is None and iy is None:
            if overflow:
                start = 0
                end_offest = 2
            else:
                start = 1
                end_offset = 1
            return [[(self.GetBinError(ix, iy), self.GetBinError(ix, iy))
                    for iy in xrange(start, self.nbins(1) + end_offset)]
                    for ix in xrange(start, self.nbins(0) + end_offset)]
        ix = ix % (self.nbins(0) + 2)
        iy = iy % (self.nbins(1) + 2)
        return (self.GetBinError(ix, iy),
                self.GetBinError(ix, iy))

    def ravel(self, name=None):
        """
        Convert 2D histogram into 1D histogram with the y-axis repeated along
        the x-axis, similar to NumPy's ravel().
        """
        nbinsx = self.nbins(0)
        nbinsy = self.nbins(1)
        left_edge = self.xedgesl(1)
        right_edge = self.xedgesh(nbinsx)
        out = Hist(nbinsx * nbinsy,
                   left_edge, nbinsy * (right_edge - left_edge) + left_edge,
                   type=self.TYPE,
                   name=name,
                   title=self.title,
                   **self.decorators)
        for i, bin in enumerate(self.bins(overflow=False)):
            out.SetBinContent(i + 1, bin.value)
            out.SetBinError(i + 1, bin.error)
        return out


class _Hist3D(_HistBase):

    DIM = 3

    def x(self, index=None, overflow=False):

        return self._centers(0, index, overflow=overflow)

    def xerravg(self, index=None, overflow=False):

        return self._erravg(0, index, overflow=overflow)

    def xerrl(self, index=None, overflow=False):

        return self._erravg(0, index, overflow=overflow)

    def xerrh(self, index=None, overflow=False):

        return self._erravg(0, index, overflow=overflow)

    def xerr(self, index=None, overflow=False):

        return self._err(0, index, overflow=overflow)

    def xwidth(self, index=None, overflow=False):

        return self._width(0, index, overflow=overflow)

    def xedgesl(self, index=None, overflow=False):

        return self._edgesl(0, index, overflow=overflow)

    def xedgesh(self, index=None, overflow=False):

        return self._edgesh(0, index, overflow=overflow)

    def xedges(self, index=None, overflow=False):

        return self._edges(0, index, overflow=overflow)

    def y(self, index=None, overflow=False):

        return self._centers(1, index, overflow=overflow)

    def yerravg(self, index=None, overflow=False):

        return self._erravg(1, index, overflow=overflow)

    def yerrl(self, index=None, overflow=False):

        return self._erravg(1, index, overflow=overflow)

    def yerrh(self, index=None, overflow=False):

        return self._erravg(1, index, overflow=overflow)

    def yerr(self, index=None, overflow=False):

        return self._err(1, index, overflow=overflow)

    def ywidth(self, index=None, overflow=False):

        return self._width(1, index, overflow=overflow)

    def yedgesl(self, index=None, overflow=False):

        return self._edgesl(1, index, overflow=overflow)

    def yedgesh(self, index=None, overflow=False):

        return self._edgesh(1, index, overflow=overflow)

    def yedges(self, index=None, overflow=False):

        return self._edges(1, index, overflow=overflow)

    def z(self, index=None, overflow=False):

        return self._centers(2, index, overflow=overflow)

    def zerravg(self, index=None, overflow=False):

        return self._erravg(2, index, overflow=overflow)

    def zerrl(self, index=None, overflow=False):

        return self._erravg(2, index, overflow=overflow)

    def zerrh(self, index=None, overflow=False):

        return self._erravg(2, index, overflow=overflow)

    def zerr(self, index=None, overflow=False):

        return self._err(2, index, overflow=overflow)

    def zwidth(self, index=None, overflow=False):

        return self._width(2, index, overflow=overflow)

    def zedgesl(self, index=None, overflow=False):

        return self._edgesl(2, index, overflow=overflow)

    def zedgesh(self, index=None, overflow=False):

        return self._edgesh(2, index, overflow=overflow)

    def zedges(self, index=None, overflow=False):

        return self._edges(2, index, overflow=overflow)

    def werrh(self, index=None, overflow=False):

        return self.werravg(index, overflow=overflow)

    def werrl(self, index=None, overflow=False):

        return self.werravg(index, overflow=overflow)

    def w(self, ix=None, iy=None, iz=None, overflow=False):

        if ix is None and iy is None and iz is None:
            if overflow:
                start = 0
                end_offset = 2
            else:
                start = 1
                end_offset = 1
            return [[[self.GetBinContent(ix, iy, iz)
                    for iz in xrange(start, self.nbins(2) + end_offset)]
                    for iy in xrange(start, self.nbins(1) + end_offset)]
                    for ix in xrange(start, self.nbins(0) + end_offset)]
        ix = ix % (self.nbins(0) + 2)
        iy = iy % (self.nbins(1) + 2)
        iz = iz % (self.nbins(2) + 2)
        return self.GetBinContent(ix, iy, iz)

    def werravg(self, ix=None, iy=None, iz=None, overflow=False):

        if ix is None and iy is None and iz is None:
            if overflow:
                start = 0
                end_offset = 2
            else:
                start = 1
                end_offset = 1
            return [[[self.GetBinError(ix, iy, iz)
                    for iz in xrange(start, self.nbins(2) + end_offset)]
                    for iy in xrange(start, self.nbins(1) + end_offset)]
                    for ix in xrange(start, self.nbins(0) + end_offset)]
        ix = ix % (self.nbins(0) + 2)
        iy = iy % (self.nbins(1) + 2)
        iz = iz % (self.nbins(2) + 2)
        return self.GetBinError(ix, iy, iz)

    def werr(self, ix=None, iy=None, iz=None, overflow=False):

        if ix is None and iy is None and iz is None:
            if overflow:
                start = 0
                end_offset = 2
            else:
                start = 1
                end_offset = 1
            return [[[
                (self.GetBinError(ix, iy, iz), self.GetBinError(ix, iy, iz))
                for iz in xrange(start, self.nbins(2) + end_offset)]
                for iy in xrange(start, self.nbins(1) + end_offset)]
                for ix in xrange(start, self.nbins(0) + end_offset)]
        ix = ix % (self.nbins(0) + 2)
        iy = iy % (self.nbins(1) + 2)
        iz = iz % (self.nbins(2) + 2)
        return (self.GetBinError(ix, iy, iz),
                self.GetBinError(ix, iy, iz))


def _Hist_class(type='F'):

    type = type.upper()
    if type not in _HistBase.TYPES:
        raise TypeError(
            "No histogram available with bin type {0}".format(type))
    rootclass = _HistBase.TYPES[type][0]

    class Hist(_Hist, rootclass):

        _ROOT = rootclass
        TYPE = type

        def __init__(self, *args, **kwargs):

            params = self._parse_args(args)
            name = kwargs.pop('name', None)
            title = kwargs.pop('title', None)

            if params[0]['bins'] is None:
                super(Hist, self).__init__(
                    params[0]['nbins'], params[0]['low'], params[0]['high'],
                    name=name, title=title)
            else:
                super(Hist, self).__init__(
                    params[0]['nbins'], array('d', params[0]['bins']),
                    name=name, title=title)

            self._post_init(**kwargs)

    return Hist


def _Hist2D_class(type='F'):

    type = type.upper()
    if type not in _HistBase.TYPES:
        raise TypeError(
            "No histogram available with bin type {0}".format(type))
    rootclass = _HistBase.TYPES[type][1]

    class Hist2D(_Hist2D, rootclass):

        _ROOT = rootclass
        TYPE = type

        def __init__(self, *args, **kwargs):

            params = self._parse_args(args)
            name = kwargs.pop('name', None)
            title = kwargs.pop('title', None)

            if params[0]['bins'] is None and params[1]['bins'] is None:
                super(Hist2D, self).__init__(
                    params[0]['nbins'], params[0]['low'], params[0]['high'],
                    params[1]['nbins'], params[1]['low'], params[1]['high'],
                    name=name, title=title)
            elif params[0]['bins'] is None and params[1]['bins'] is not None:
                super(Hist2D, self).__init__(
                    params[0]['nbins'], params[0]['low'], params[0]['high'],
                    params[1]['nbins'], array('d', params[1]['bins']),
                    name=name, title=title)
            elif params[0]['bins'] is not None and params[1]['bins'] is None:
                super(Hist2D, self).__init__(
                    params[0]['nbins'], array('d', params[0]['bins']),
                    params[1]['nbins'], params[1]['low'], params[1]['high'],
                    name=name, title=title)
            else:
                super(Hist2D, self).__init__(
                    params[0]['nbins'], array('d', params[0]['bins']),
                    params[1]['nbins'], array('d', params[1]['bins']),
                    name=name, title=title)

            self._post_init(**kwargs)

    return Hist2D


def _Hist3D_class(type='F'):

    type = type.upper()
    if type not in _HistBase.TYPES:
        raise TypeError(
            "No histogram available with bin type {0}".format(type))
    rootclass = _HistBase.TYPES[type][2]

    class Hist3D(_Hist3D, rootclass):

        _ROOT = rootclass
        TYPE = type

        def __init__(self, *args, **kwargs):

            params = self._parse_args(args)
            name = kwargs.pop('name', None)
            title = kwargs.pop('title', None)

            # ROOT is missing constructors for TH3...
            if (params[0]['bins'] is None and
                    params[1]['bins'] is None and
                    params[2]['bins'] is None):
                super(Hist3D, self).__init__(
                    params[0]['nbins'], params[0]['low'], params[0]['high'],
                    params[1]['nbins'], params[1]['low'], params[1]['high'],
                    params[2]['nbins'], params[2]['low'], params[2]['high'],
                    name=name, title=title)
            else:
                if params[0]['bins'] is None:
                    step = ((params[0]['high'] - params[0]['low'])
                            / float(params[0]['nbins']))
                    params[0]['bins'] = [
                        params[0]['low'] + n * step
                        for n in xrange(params[0]['nbins'] + 1)]
                if params[1]['bins'] is None:
                    step = ((params[1]['high'] - params[1]['low'])
                            / float(params[1]['nbins']))
                    params[1]['bins'] = [
                        params[1]['low'] + n * step
                        for n in xrange(params[1]['nbins'] + 1)]
                if params[2]['bins'] is None:
                    step = ((params[2]['high'] - params[2]['low'])
                            / float(params[2]['nbins']))
                    params[2]['bins'] = [
                        params[2]['low'] + n * step
                        for n in xrange(params[2]['nbins'] + 1)]
                super(Hist3D, self).__init__(
                    params[0]['nbins'], array('d', params[0]['bins']),
                    params[1]['nbins'], array('d', params[1]['bins']),
                    params[2]['nbins'], array('d', params[2]['bins']),
                    name=name, title=title)

            self._post_init(**kwargs)

    return Hist3D


_HIST_CLASSES_1D = {}
_HIST_CLASSES_2D = {}
_HIST_CLASSES_3D = {}

for bintype in _HistBase.TYPES.keys():
    cls = _Hist_class(type=bintype)
    snake_case_methods(cls)
    _HIST_CLASSES_1D[bintype] = cls

    cls = _Hist2D_class(type=bintype)
    snake_case_methods(cls)
    _HIST_CLASSES_2D[bintype] = cls

    cls = _Hist3D_class(type=bintype)
    snake_case_methods(cls)
    _HIST_CLASSES_3D[bintype] = cls


class Hist(_Hist, QROOT.TH1):
    """
    Returns a 1-dimensional Hist object which inherits from the associated
    ROOT.TH1* class (where * is C, S, I, F, or D depending on the type
    keyword argument)
    """
    _ROOT = QROOT.TH1

    @classmethod
    def dynamic_cls(cls, type='F'):

        return _HIST_CLASSES_1D[type]

    def __new__(cls, *args, **kwargs):

        type = kwargs.pop('type', 'F').upper()
        return cls.dynamic_cls(type)(
            *args, **kwargs)


# alias Hist1D -> Hist
Hist1D = Hist


class Hist2D(_Hist2D, QROOT.TH2):
    """
    Returns a 2-dimensional Hist object which inherits from the associated
    ROOT.TH1* class (where * is C, S, I, F, or D depending on the type
    keyword argument)
    """
    _ROOT = QROOT.TH2

    @classmethod
    def dynamic_cls(cls, type='F'):

        return _HIST_CLASSES_2D[type]

    def __new__(cls, *args, **kwargs):

        type = kwargs.pop('type', 'F').upper()
        return cls.dynamic_cls(type)(
            *args, **kwargs)


class Hist3D(_Hist3D, QROOT.TH3):
    """
    Returns a 3-dimensional Hist object which inherits from the associated
    ROOT.TH1* class (where * is C, S, I, F, or D depending on the type
    keyword argument)
    """
    _ROOT = QROOT.TH3

    @classmethod
    def dynamic_cls(cls, type='F'):

        return _HIST_CLASSES_3D[type]

    def __new__(cls, *args, **kwargs):

        type = kwargs.pop('type', 'F').upper()
        return cls.dynamic_cls(type)(
            *args, **kwargs)


class HistStack(Plottable, NamedObject, QROOT.THStack):

    _ROOT = QROOT.THStack

    def __init__(self, name=None, title=None, hists=None, **kwargs):

        super(HistStack, self).__init__(name=name, title=title)
        self._post_init(hists=hists, **kwargs)

    def _post_init(self, hists=None, **kwargs):

        super(HistStack, self)._post_init(**kwargs)

        self.hists = []
        self.dim = 1
        current_hists = super(HistStack, self).GetHists()
        if current_hists:
            for i, hist in enumerate(current_hists):
                hist = asrootpy(hist)
                if i == 0:
                    self.dim = dim(hist)
                elif dim(hist) != self.dim:
                    raise TypeError(
                        "Dimensions of the contained histograms are not equal")
                self.hists.append(hist)

        self.sum = sum(self.hists) if self.hists else None

        if hists:
            for h in hists:
                self.Add(h)

    def __dim__(self):

        return self.dim

    def GetHists(self):

        return [hist for hist in self.hists]

    def Add(self, hist):

        if isinstance(hist, _Hist) or isinstance(hist, _Hist2D):
            if not self:
                self.dim = dim(hist)
                self.sum = hist.Clone()
            elif dim(self) != dim(hist):
                raise TypeError(
                    "Dimension of histogram does not match dimension "
                    "of already contained histograms")
            else:
                self.sum += hist
            self.hists.append(hist)
            super(HistStack, self).Add(hist, hist.drawstyle)
        else:
            raise TypeError(
                "Only 1D and 2D histograms are supported")

    def __add__(self, other):

        if not isinstance(other, HistStack):
            raise TypeError(
                "Addition not supported for HistStack and {0}".format(
                    other.__class__.__name__))
        clone = HistStack()
        for hist in self:
            clone.Add(hist)
        for hist in other:
            clone.Add(hist)
        return clone

    def __iadd__(self, other):

        if not isinstance(other, HistStack):
            raise TypeError(
                "Addition not supported for HistStack and {0}".format(
                    other.__class__.__name__))
        for hist in other:
            self.Add(hist)
        return self

    def __len__(self):

        return len(self.GetHists())

    def __getitem__(self, index):

        return self.GetHists()[index]

    def __iter__(self):

        for hist in self.hists:
            yield hist

    def __nonzero__(self):

        return len(self) != 0

    def __cmp__(self, other):

        diff = self.max() - other.max()
        if diff > 0:
            return 1
        if diff < 0:
            return -1
        return 0

    def Scale(self, value):

        for hist in self:
            hist.Scale(value)

    def Integral(self, start=None, end=None):

        integral = 0
        if start is not None and end is not None:
            for hist in self:
                integral += hist.Integral(start, end)
        else:
            for hist in self:
                integral += hist.Integral()
        return integral

    def lowerbound(self, axis=0):

        if not self:
            return None  # negative infinity
        return min(hist.lowerbound(axis=axis) for hist in self)

    def upperbound(self, axis=0):

        if not self:
            return ()  # positive infinity
        return max(hist.upperbound(axis=axis) for hist in self)

    def max(self, include_error=False):

        if not self:
            return 0
        return self.sum.max(include_error=include_error)

    def min(self, include_error=False):

        if not self:
            return 0
        return self.sum.min(include_error=include_error)

    def Clone(self, newName=None):

        clone = HistStack(name=newName,
                          title=self.GetTitle(),
                          **self.decorators)
        for hist in self:
            clone.Add(hist.Clone())
        return clone


@snake_case_methods
class Efficiency(Plottable, NamedObject, QROOT.TEfficiency):

    _ROOT = QROOT.TEfficiency

    def __init__(self, passed, total, name=None, title=None, **kwargs):

        if passed.GetDimension() != 1 or total.GetDimension() != 1:
            raise TypeError(
                "histograms must be 1 dimensional")
        if len(passed) != len(total):
            raise ValueError(
                "histograms must have the same number of bins")
        if list(passed.xedges()) != list(total.xedges()):
            raise ValueError(
                "histograms do not have the same bin boundaries")

        super(Efficiency, self).__init__(
            len(total), total.xedgesl(1), total.xedgesh(total.nbins(0)),
            name=name, title=title)

        self.passed = passed.Clone()
        self.total = total.Clone()
        self.SetPassedHistogram(self.passed, 'f')
        self.SetTotalHistogram(self.total, 'f')
        self._post_init(**kwargs)

    def __len__(self):

        return len(self.total)

    def __getitem__(self, idx):

        return self.GetEfficiency(idx)

    def __add__(self, other):

        copy = self.Clone()
        copy.Add(other)
        return copy

    def __iadd__(self, other):

        super(Efficiency, self).Add(self, other)
        return self

    def __iter__(self):

        for idx in xrange(len(self) + 2):
            yield self.GetEfficiency(idx)

    def efficiencies(self, overflow=False):

        if overflow:
            start = 0
            end = len(self) + 2
        else:
            start = 1
            end = len(self) + 1
        for idx in xrange(start, end):
            yield self.GetEfficiency(idx)

    def errors(self, overflow=False):

        if overflow:
            start = 0
            end = len(self) + 2
        else:
            start = 1
            end = len(self) + 1
        for idx in xrange(start, end):
            yield (
                self.GetEfficiencyErrorLow(idx),
                self.GetEfficiencyErrorUp(idx))

    def GetGraph(self, overflow=False):

        if overflow:
            start = 0
            end = len(self) + 2
        else:
            start = 1
            end = len(self) + 1
        graph = Graph(end - start)
        for index, (idx, effic, (low, up)) in enumerate(
                izip(xrange(start, end),
                     self.efficiencies(overflow=overflow),
                     self.errors(overflow=overflow))):
            graph.SetPoint(index, self.total.x(index), effic)
            xerror = self.total.xwidth(index) / 2.
            graph.SetPointError(index, xerror, xerror, low, up)
        return graph

    @property
    def painted_graph(self):
        """
        Returns the painted graph for a TEfficiency, or if it isn't
        available, generates one on an `invisible_canvas`.
        """
        if not self.GetPaintedGraph():
            with invisible_canvas():
                self.Draw()
        assert self.GetPaintedGraph(), (
            "Failed to create TEfficiency::GetPaintedGraph")
        the_graph = asrootpy(self.GetPaintedGraph())
        # Ensure it has the same style as this one.
        the_graph.decorate(**self.decorators)
        return the_graph


def histogram(data, *args, **kwargs):
    """
    Create and fill a one-dimensional histogram.

    The same arguments as the ``Hist`` class are expected.
    If the number of bins and the ranges are not specified they are
    automatically deduced with the ``autobinning`` function using the method
    specified by the ``binning`` argument. Only one-dimensional histogramming
    is supported.
    """
    from .autobinning import autobinning
    dim = kwargs.pop('dim', 1)
    if dim != 1:
        raise NotImplementedError
    if 'binning' in kwargs:
        args = autobinning(data, kwargs['binning'])
        del kwargs['binning']

    histo = Hist(*args, **kwargs)
    for d in data:
        histo.Fill(d)
    return list(histo.xedgesl()), histo
