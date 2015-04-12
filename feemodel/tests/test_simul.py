from __future__ import division

import unittest
from collections import Counter
from copy import deepcopy
from random import seed

from feemodel.txmempool import MemBlock
from feemodel.simul import (SimPool, SimPools, Simul, SimTx, SimTxSource,
                            SimEntry)
# from feemodel.simul.txsources import TxPtrArray
from feemodel.simul.pools import SimBlock
from feemodel.tests.config import memblock_dbfile as dbfile
from feemodel.simul.simul import SimMempool
from feemodel.util import cumsum_gen

seed(0)

ref_pools = {
    'pool0': SimPool(0.2, 500000, 20000),
    'pool1': SimPool(0.3, 750000, 10000),
    'pool2': SimPool(0.5, 1000000, 1000)
}

ref_txsample = [
    SimTx(11000, 640),
    SimTx(40000, 250),
    SimTx(2000, 500)]
ref_txrate = 1.1
ref_mean_byterate = sum([
    tx.size for tx in ref_txsample])/float(len(ref_txsample))*ref_txrate

# b = MemBlock.read(333931, dbfile=dbfile)
init_entries = MemBlock.read(333931, dbfile=dbfile).entries
# init_mempool = [SimEntry.from_mementry(txid, entry)
#                 for txid, entry in b.entries.items()]
print("Mempool size is %d" %
      sum([entry.size for entry in init_entries.values()]))


class PoolSimTests(unittest.TestCase):

    def test_randompool(self):
        simpools = SimPools(ref_pools)
        numiters = 10000
        poolnames = []
        for idx, (simblock, blockinterval) in enumerate(
                simpools.get_blockgen()):
            if idx >= numiters:
                break
            poolnames.append(simblock.poolname)

        c = Counter(poolnames)
        totalhashrate = sum(
            pool.hashrate for pool in simpools.pools.values())
        for name, pool in simpools.pools.items():
            count = float(c[name])
            proportion = pool.hashrate / totalhashrate
            diff = abs(proportion - count/numiters)
            self.assertLess(diff, 0.01)

    def test_caps(self):
        simpools = SimPools(ref_pools)
        feerates, caps = simpools.get_capacity()
        ref_feerates = [0, 1000, 10000, 20000]
        ref_caps = list(cumsum_gen(
            [0, 0.5*1000000/600, 0.3*750000/600, 0.2*500000/600]))
        self.assertEqual(feerates, ref_feerates)
        self.assertEqual(caps, ref_caps)

        # Duplicate minfeerate
        newref_pools = deepcopy(ref_pools)
        newref_pools.update({'pool3': SimPool(0.1, 600000, 1000)})
        newref_pools['pool1'].hashrate = 0.2
        simpools = SimPools(newref_pools)
        feerates, caps = simpools.get_capacity()
        ref_feerates = [0, 1000, 10000, 20000]
        ref_caps = list(cumsum_gen(
            [0, 0.5*1000000/600 + 0.1*600000/600,
             0.2*750000/600, 0.2*500000/600]))
        self.assertEqual(feerates, ref_feerates)
        self.assertEqual(caps, ref_caps)

        # Inf minfeerate
        newref_pools = deepcopy(ref_pools)
        newref_pools['pool0'].minfeerate = float("inf")
        simpools = SimPools(newref_pools)
        feerates, caps = simpools.get_capacity()
        ref_feerates = [0, 1000, 10000]
        ref_caps = list(cumsum_gen(
            [0, 0.5*1000000/600, 0.3*750000/600]))
        self.assertEqual(feerates, ref_feerates)
        self.assertEqual(caps, ref_caps)

        # Only inf minfeerate
        newref_pools = deepcopy(ref_pools)
        for pool in newref_pools.values():
            pool.minfeerate = float("inf")
        simpools = SimPools(newref_pools)
        feerates, caps = simpools.get_capacity()
        ref_feerates = [0]
        ref_caps = [0]
        self.assertEqual(feerates, ref_feerates)
        self.assertEqual(caps, ref_caps)

        # Empty pools
        simpools = SimPools({})
        with self.assertRaises(ValueError):
            simpools.get_capacity()


class TxSourceTests(unittest.TestCase):

    def setUp(self):
        self.tx_source = SimTxSource(ref_txsample, ref_txrate)
        self.feerates = [0, 2000, 10999, 20000]
        byterates_binned = [
            0, 500*ref_txrate/3., 640*ref_txrate/3., 250*ref_txrate/3.]
        self.ref_byterates = list(cumsum_gen(reversed(byterates_binned)))
        self.ref_byterates.reverse()
        # self.ref_byterates = [sum(byterates_binned[idx:])
        #                      for idx in range(len(byterates_binned))]

    def test_print_rates(self):
        self.tx_source.print_rates()
        self.tx_source.print_rates(self.feerates)

    def test_get_byterates(self):
        print("Ref byterates:")
        for feerate, byterate in zip(self.feerates, self.ref_byterates):
            print("{}\t{}".format(feerate, byterate))
        _dum, byterates = self.tx_source.get_byterates(self.feerates)
        for test, target in zip(byterates, self.ref_byterates):
            self.assertAlmostEqual(test, target)
        _dum, byterates = self.tx_source.get_byterates()
        for test, target in zip(byterates, self.ref_byterates):
            self.assertAlmostEqual(test, target)

    def test_emitter(self):
        t = 10000.
        mempool = SimMempool({})
        tx_emitter = self.tx_source.get_emitter(mempool, feeratethresh=2000)
        # Emit txs over an interval of t seconds.
        tx_emitter(t)
        simtxs = mempool.get_entries().values()

        # Compare the tx rate.
        txrate = len(simtxs) / t
        diff = abs(txrate - ref_txrate)
        self.assertLess(diff, 0.01)

        # Check that byterates match.
        derivedsource = SimTxSource(simtxs, txrate)
        _dum, byterates = derivedsource.get_byterates(self.feerates)
        for test, target in zip(byterates, self.ref_byterates):
            diff = abs(test - target)
            self.assertLess(diff, 10)

    def test_feerate_threshold(self):
        t = 10000.
        # emitted = TxPtrArray()
        mempool = SimMempool({})
        tx_emitter = self.tx_source.get_emitter(mempool, feeratethresh=2001)
        # Emit txs over an interval of t seconds.
        tx_emitter(t)
        simtxs = mempool.get_entries().values()

        # Compare the tx rate.
        txrate = len(simtxs) / t
        # We filtered out 1 out of 3 SimTxs by using feeratethresh = 2001
        ref_txrate_mod = ref_txrate * 2 / 3
        diff = abs(txrate - ref_txrate_mod)
        self.assertLess(diff, 0.01)

        # Check that byterates match.
        derivedsource = SimTxSource(simtxs, txrate)
        _dum, byterates = derivedsource.get_byterates(self.feerates)
        # print("Derived byterates are {}.".format(byterates))
        for idx, (test, target) in enumerate(
                zip(byterates, self.ref_byterates)):
            if idx > 1:
                diff = abs(test - target)
                self.assertLess(diff, 10)

        # Test thresh equality
        mempool.reset()
        tx_emitter = self.tx_source.get_emitter(mempool, feeratethresh=2000)
        # Emit txs over an interval of t seconds.
        tx_emitter(t)
        simtxs = mempool.get_entries().values()

        # Compare the tx rate.
        txrate = len(simtxs) / t
        # We filtered out 1 out of 3 SimTxs by using feeratethresh = 2001
        diff = abs(txrate - ref_txrate)
        self.assertLess(diff, 0.01)

        # Test inf thresh
        mempool.reset()
        tx_emitter = self.tx_source.get_emitter(mempool,
                                                feeratethresh=float("inf"))
        tx_emitter(t)
        self.assertEqual(len(mempool.get_entries()), 0)

    def test_zero_interval(self):
        mempool = SimMempool({})
        tx_emitter = self.tx_source.get_emitter(mempool)
        tx_emitter(0)
        self.assertEqual(len(mempool.get_entries()), 0)

    def test_zero_txrate(self):
        self.tx_source = SimTxSource(ref_txsample, 0)
        mempool = SimMempool({})
        tx_emitter = self.tx_source.get_emitter(mempool)
        tx_emitter(600)
        self.assertEqual(len(mempool.get_entries()), 0)
        # TODO: fix the display when printing with zero txrate
        self.tx_source.print_rates()

    def test_empty_txsample(self):
        mempool = SimMempool({})
        self.tx_source = SimTxSource([], ref_txrate)
        with self.assertRaises(ValueError):
            self.tx_source.get_emitter(mempool)
        with self.assertRaises(ValueError):
            self.tx_source.get_byterates()
        with self.assertRaises(ValueError):
            self.tx_source.calc_mean_byterate()
        self.tx_source = SimTxSource([], 0)
        tx_emitter = self.tx_source.get_emitter(mempool)
        tx_emitter(600)
        self.assertEqual(len(mempool.get_entries()), 0)


class BasicSimTests(unittest.TestCase):

    def setUp(self):
        self.simpools = SimPools(pools=ref_pools)
        self.tx_source = SimTxSource(ref_txsample, ref_txrate)
        self.sim = Simul(self.simpools, self.tx_source)
        self.init_entries = deepcopy(init_entries)

    def test_basic(self):
        print("Basic Sim: the stable feerate is %d." % self.sim.stablefeerate)
        print("Height\tNumtxs\tSize\tSFR\tMPsize")
        for idx, simblock in enumerate(self.sim.run()):
            if idx >= 50:
                break
            mempoolsize = sum([entry.size for entry in
                               self.sim.mempool.get_entries().values()])
            print("%d\t%d\t%d\t%.0f\t%d" % (idx, len(simblock.txs),
                                            simblock.size, simblock.sfr,
                                            mempoolsize))

        self.sim.cap.print_cap()

    def test_mempool(self):
        for entry in self.init_entries.values():
            entry.feerate = 100000
            entry.size = 9927
        print("With init mempool:")
        print("Height\tNumtxs\tSize\tSFR\tMPsize")
        for idx, simblock in enumerate(
                self.sim.run(init_entries=self.init_entries)):
            if idx >= 50:
                break
            mempoolsize = sum([entry.size for entry in
                               self.sim.mempool.get_entries().values()])
            self.assertEqual(simblock.size,
                             sum([tx.size for tx in simblock.txs]))
            print("%d\t%d\t%d\t%.0f\t%d" % (idx, len(simblock.txs),
                                            simblock.size, simblock.sfr,
                                            mempoolsize))
        self.sim.cap.print_cap()

    def test_degenerate_pools(self):
        pass
        # self.ref_pools = {'pool0': SimPool(1, 0, float("inf")),
        #                   'pool1': SimPool(1, 0, 0)}
        # # TODO: fix outdated stablefeerate calcs
        # # Raises ValueError because not enough capacity.
        # # self.assertRaises(ValueError, Simul, SimPools(self.ref_pools),
        # #                   self.tx_source)
        # self.ref_pools.update({'pool2': SimPool(3, 1000000, 1000)})
        # self.sim = Simul(SimPools(self.ref_pools), self.tx_source)
        # print("Degenerate pools:")
        # print("Height\tNumtxs\tSize\tSFR")
        # for simblock in self.sim.run():
        #     if simblock.height >= 50:
        #         break
        #     print("%d\t%d\t%d\t%.0f" % (simblock.height, len(simblock.txs),
        #                                 simblock.size, simblock.sfr))
        # self.sim.cap.print_cap()


class CustomMempoolTests(unittest.TestCase):
    # TODO: needs more detailed tests

    def setUp(self):
        pools = PseudoPools()
        tx_source = SimTxSource(ref_txsample, 0)
        self.sim = Simul(pools, tx_source)

    def test_A(self):
        print("Test A:")
        print("=======")
        init_entries = {
            str(i): SimEntry(100000, 250, depends=['0'])
            for i in range(1, 1000)
        }
        init_entries['0'] = SimEntry(100000, 1000000)
        for simblock in self.sim.run(init_entries=init_entries):
            print(simblock)
            print('MBS: %d, MFR: %d' % (simblock.pool.maxblocksize,
                                        simblock.pool.minfeerate))
            self.assertEqual(len(simblock.txs), 1)
            self.assertEqual(simblock.sfr, 100001)
            self.assertEqual(len(self.sim.mempool.get_entries()), 999)
            break

    def test_B(self):
        print("Test B:")
        print("=======")
        init_entries = {
            str(i): SimEntry(100000, 250, depends=['0'])
            for i in range(1, 1000)
        }
        init_entries['0'] = SimEntry(999, 250)
        for simblock in self.sim.run(init_entries=init_entries):
            print('MBS: %d, MFR: %d' % (simblock.pool.maxblocksize,
                                        simblock.pool.minfeerate))
            self.assertEqual(len(simblock.txs), 0)
            self.assertEqual(simblock.sfr, 1000)
            self.assertEqual(len(self.sim.mempool.get_entries()), 1000)
            break

    def test_C(self):
        print("Test C:")
        print("=======")
        init_entries = {
            str(i): SimEntry(100000, 250, depends=['0'])
            for i in range(1, 1000)
        }
        init_entries['0'] = SimEntry(1000, 900000)
        for simblock in self.sim.run(init_entries=init_entries):
            print('MBS: %d, MFR: %d' % (simblock.pool.maxblocksize,
                                        simblock.pool.minfeerate))
            self.assertEqual(len(simblock.txs), 401)
            self.assertEqual(simblock.sfr, 1001)
            self.assertEqual(len(self.sim.mempool.get_entries()), 599)
            break

    def test_D(self):
        print("Test D:")
        print("=======")
        # Chain of txs
        init_entries = {
            str(i): SimEntry(10500-i, 2000, depends=[str(i+1)])
            for i in range(1000)
        }
        # init_mempool = [SimEntry(str(i), SimTx(10500-i, 2000), [str(i+1)])
        #                 for i in range(1000)]
        with self.assertRaises(ValueError):
            # Hanging dependency
            for simblock in self.sim.run(init_entries=init_entries):
                break

        init_entries['1000'] = SimEntry(1001, 2000)
        # init_mempool.append(SimEntry('1000', SimTx(1001, 2000)))
        for idx, simblock in enumerate(
                self.sim.run(init_entries=init_entries)):
            if idx == 0:
                self.assertEqual(simblock.sfr, 1002)
                self.assertEqual(max([tx.feerate for tx in simblock.txs]),
                                 9999)
                self.assertEqual(len(simblock.txs), 500)
                self.assertEqual(len(self.sim.mempool.get_entries()), 501)
            elif idx == 1:
                self.assertEqual(simblock.sfr, 10001)
                self.assertEqual(len(simblock.txs), 375)
                self.assertEqual(len(self.sim.mempool.get_entries()), 501-375)
                self.assertEqual(simblock.size, 750000)
            elif idx == 2:
                self.assertEqual(simblock.sfr, 20000)
                self.assertEqual(len(simblock.txs), 0)
                self.assertEqual(len(self.sim.mempool.get_entries()), 501-375)
                self.assertEqual(simblock.size, 0)
            elif idx == 3:
                self.assertEqual(simblock.sfr, 1000)
                self.assertEqual(len(simblock.txs), 501-375)
                self.assertEqual(len(self.sim.mempool.get_entries()), 0)
                self.assertEqual(simblock.size, 2000*(501-375))
            else:
                break


class PseudoPools(SimPools):
    """SimPools with deterministic blockgen."""

    def __init__(self):
        super(PseudoPools, self).__init__(pools=ref_pools)

    def get_blockgen(self):
        def blockgenfn():
            poolitems = sorted(self.pools.items(),
                               key=lambda poolitem: poolitem[1].hashrate,
                               reverse=True)
            numpools = len(poolitems)
            idx = 0
            while True:
                poolname, pool = poolitems[idx % numpools]
                blockinterval = 600
                simblock = SimBlock(poolname, pool)
                yield simblock, blockinterval
                idx += 1
        return blockgenfn()


if __name__ == '__main__':
    unittest.main()
