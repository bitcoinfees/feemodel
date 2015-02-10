from __future__ import division

from math import ceil
from feemodel.util import interpolate, Table, DataSample


class SimStats(object):
    def __init__(self):
        self.timestamp = 0.
        self.timespent = None
        self.numiters = None
        self.cap = None
        self.stablefeerate = None

    def print_stats(self):
        if self:
            name = self.__class__.__name__
            print(("{}\n" + "="*len(name)).format(name))
            print("Num iters: %d" % self.numiters)
            print("Time spent: %.2f" % self.timespent)
            print("Stable feerate: %d" % self.stablefeerate)
            self.cap.print_cap()

    def get_stats(self):
        if not self:
            return None

    def __nonzero__(self):
        return bool(self.timestamp)


class WaitFn(object):
    '''Wait times as a function of feerate.'''

    def __init__(self, feerates, waits, errors=None):
        self.feerates = feerates
        self.waits = waits
        self.errors = errors

    def __call__(self, feerate):
        '''Evaluate the function at feerate.

        Returns the linear interpolated value of the function. If feerate
        is lower than all available feerate datapoints, returns None. If it is
        larger, return the boundary value of the function.
        '''
        t, idx = interpolate(feerate, self.feerates, self.waits)
        return t if idx else None

    def inv(self, wait):
        '''Inverse of self.__call__.
        If wait is smaller than all available wait datapoints, returns None.
        If larger, return the boundary value of the function.
        '''
        t, idx = interpolate(wait, self.waits[-1::-1], self.feerates[-1::-1])
        return t if idx else None

    def print_fn(self):
        table = Table()
        table.add_row(('Feerate', 'Wait', 'Error'))
        for idx in range(len(self.feerates)):
            table.add_row((
                self.feerates[idx],
                '%.2f' % self.waits[idx],
                '%.2f' % self.errors[idx] if self.errors else '-'
            ))
        table.print_table()


class Capacity(object):
    def __init__(self, feerates, tx_byterates, cap_lower, cap_upper):
        self.feerates = feerates
        self.tx_byterates = tx_byterates
        self.cap_lower = cap_lower
        self.cap_upper = cap_upper

    def calc_stablefeerate(self, rate_ratio_thresh):
        stablefeerate = None
        for idx in range(len(self.feerates)):
            if not self.cap_lower[idx]:
                continue
            rate_ratio = self.tx_byterates[idx] / self.cap_lower[idx]
            if rate_ratio <= rate_ratio_thresh:
                stablefeerate = self.feerates[idx]
                break
        return stablefeerate

    def print_cap(self):
        table = Table()
        table.add_row(("Feerate", "TxByteRate", "Cap (lower)", "Cap (upper)"))
        for idx in range(len(self.feerates)):
            table.add_row((
                self.feerates[idx],
                '%.2f' % self.tx_byterates[idx],
                '%.2f' % self.cap_lower[idx],
                '%.2f' % self.cap_upper[idx]))
        table.print_table()


def get_feeclasses(cap, tx_source, stablefeerate):
    '''Choose suitable feerates at which to evaluate stats.'''
    feerates = cap.feerates[1:]
    caps = cap.cap_lower
    capsdiff = [caps[idx] - caps[idx-1]
                for idx in range(1, len(feerates)+1)]
    feeDS = DataSample(feerates)
    feeclasses = [feeDS.get_percentile(p/100., weights=capsdiff)
                  for p in range(5, 100, 5)]
    # Round up to nearest 200 satoshis
    quantize = 200
    feeclasses = [int(ceil((feerate + 1) / quantize)*quantize)
                  for feerate in feeclasses]
    feeclasses = sorted(set(feeclasses))

    new_feeclasses = [True]
    while new_feeclasses:
        byterates = tx_source.get_byterates(feeclasses)
        # The byterate in each feeclass should not exceed 0.1 of the total
        byteratethresh = 0.1 * byterates[0]
        new_feeclasses = []
        for idx in range(len(byterates)-1):
            byteratediff = byterates[idx] - byterates[idx+1]
            if byteratediff > byteratethresh:
                feegap = feeclasses[idx+1] - feeclasses[idx]
                if feegap > 1:
                    new_feeclasses.append(feeclasses[idx] + int(feegap/2))
        feeclasses.extend(new_feeclasses)
        feeclasses.sort()

    feeclasses = filter(lambda fee: fee >= stablefeerate, feeclasses)

    return feeclasses


# #def _get_feeclasses(cap):
# #    feerates = cap.feerates[1:]
# #    caps = cap.caps
# #    capsdiff = [caps[idx] - caps[idx-1]
# #                for idx in range(1, len(feerates)+1)]
# #    feeDS = DataSample(feerates)
# #    feeclasses = [feeDS.get_percentile(p/100., weights=capsdiff)
# #                  for p in range(5, 100, 5)]
# #    feeclasses = sorted(set(feeclasses))
# #    return feeclasses


# #def transient(mempool, pools, tx_source,
# #              maxiters=10000, maxtime=60, feeclasses=None):
# #    sim = Simul(pools, tx_source)
# #    if not feeclasses:
# #        feeclasses = _get_feeclasses(sim.cap)
# #    else:
# #        feeclasses.sort()
# #    feeclasses = filter(lambda fee: fee >= sim.stablefeerate, feeclasses)
# #    tstats = {feerate: DataSample() for feerate in feeclasses}
# #
# #    simtime = 0.
# #    stranded = set(feeclasses)
# #    numiters = 0
# #    for block, realtime in sim.run(mempool=mempool, maxiters=float("inf"),
# #                                   maxtime=maxtime):
# #        simtime += block.interval
# #        stranding_feerate = block.sfr
# #
# #        for feerate in list(stranded):
# #            if feerate >= stranding_feerate:
# #                tstats[feerate].add_datapoints([simtime])
# #                stranded.remove(feerate)
# #
# #        if not stranded:
# #            numiters += 1
# #            if numiters >= maxiters:
# #                break
# #            else:
# #                simtime = 0.
# #                stranded = set(feeclasses)
# #                sim.mempool.reset()
# #
# #    return TransientStats(tstats, sim.cap, realtime, numiters,
# #                          sim.stablefeerate)
# #
# #
# #def steadystate(pools, tx_source,
# #                maxiters=100000, maxtime=600, feeclasses=None):
# #    sim = Simul(pools, tx_source)
# #    if not feeclasses:
# #        feeclasses = _get_feeclasses(sim.cap)
# #    else:
# #        feeclasses.sort()
# #    feeclasses = filter(lambda fee: fee >= sim.stablefeerate, feeclasses)
# #    qstats = QueueStats(feeclasses)
# #
# #    for block, realtime in sim.run(maxiters=maxiters, maxtime=maxtime):
# #        qstats.next_block(block.height, block.interval, block.sfr)
# #
# #    return SteadyStateStats(qstats, sim.cap, realtime,
# #                            block.height+1, sim.stablefeerate)
# #
# #
# #class SteadyStateStats(SimStats):
# #    def __init__(self, *args):
# #        super(self.__class__, self).__init__(*args)
# #        self.stats = filter(lambda qc: qc.feerate >= self.stablefeerate,
# #                            self.stats.stats)
# #
# #    def print_stats(self):
# #        super(self.__class__, self).print_stats()
# #        table = Table()
# #        table.add_row(('Feerate', 'Avgwait', 'SP', 'ASB'))
# #        for qc in self.stats:
# #            table.add_row((
# #                qc.feerate,
# #                '%.2f' % qc.avgwait,
# #                '%.2f' % qc.stranded_proportion,
# #                '%.2f' % qc.avg_strandedblocks,
# #            ))
# #        table.print_table()
# #
# #
# #class TransientStats(SimStats):
# #    def __init__(self, *args):
# #        super(self.__class__, self).__init__(*args)
# #        for feerate, twait in self.stats.items():
# #            if twait.n > 1:
# #                twait.calc_stats()
# #            else:
# #                del self.stats[feerate]
# #
# #    def print_stats(self):
# #        super(self.__class__, self).print_stats()
# #        sitems = sorted(self.stats.items())
# #        table = Table()
# #        table.add_row(('Feerate', 'Avgwait', 'Error'))
# #        for feerate, twait in sitems:
# #            table.add_row((
# #                feerate,
# #                '%.2f' % twait.mean,
# #                '%.2f' % (twait.mean_interval[1] - twait.mean)
# #            ))
# #        table.print_table()