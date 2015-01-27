from math import sqrt, cos, exp, log, pi
from bisect import bisect
from random import random


class SimTx(object):
    def __init__(self, txid, size, feerate):
        self.txid = txid
        self.size = size
        self.feerate = feerate

    def __cmp__(self, other):
        return cmp(self.feerate, other.feerate)

    def __repr__(self):
        return "SimTx{txid: %s, size: %d, feerate: %d}" % (
            self.txid, self.size, self.feerate)


class TxSource(object):
    def __init__(self, txsample, txrate):
        self.txsample = txsample
        self.txrate = txrate

    def generate_txs(self, time_interval):
        k = poisson_sample(self.txrate*time_interval)
        n = len(self.txsample)

        return [self.txsample[int(random()*n)] for i in range(k)]

    def get_byterates(self, feerates):
        n = float(len(self.txsample))
        byterates = [0.]*len(feerates)
        for tx in self.txsample:
            fee_idx = bisect(feerates, tx.feerate)
            if fee_idx > 0:
                byterates[fee_idx-1] += self.txrate*tx.size/n
        return byterates


def poisson_sample(l):
    # http://en.wikipedia.org/wiki/Poisson_distribution
    # #Generating_Poisson-distributed_random_variables
    if l > 30:
        return int(round(poisson_approx(l)))
    L = exp(-l)
    k = 0
    p = 1
    while p > L:
        k += 1
        p *= random()
    return k - 1


def poisson_approx(l):
    # box-muller
    u = random()
    v = random()

    z = sqrt(-2*log(u))*cos(2*pi*v)
    return z*sqrt(l) + l