# Copyright (C) 2009-2011 Power System Engineering Research Center
# Copyright (C) 2010-2011 Richard Lincoln
#
# PYPOWER is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published
# by the Free Software Foundation, either version 3 of the License,
# or (at your option) any later version.
#
# PYPOWER is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with PYPOWER. If not, see <http://www.gnu.org/licenses/>.

from sys import stdout, stderr

from numpy import array, arange, pi, zeros, r_, c_

from pypower.ppver import ppver
from pypower.dcopf_solver import dcopf_solver
from pypower.pipsopf_solver import pipsopf_solver
from pypower.ipoptopf_solver import ipoptopf_solver
from pypower.update_mupq import update_mupq
from pypower.makeYbus import makeYbus
from pypower.opf_consfcn import opf_consfcn
from pypower.opf_costfcn import opf_costfcn

from pypower.idx_bus import VM
from pypower.idx_gen import GEN_BUS, VG
from pypower.idx_brch import MU_ANGMIN, MU_ANGMAX


def opf_execute(om, ppopt):
    """Executes the OPF specified by an OPF model object.

    RESULTS are returned with internal indexing, all equipment
    in-service, etc.

    @see: C{opf}, C{opf_setup}
    @see: U{http://www.pserc.cornell.edu/matpower/}
    """
    ##-----  setup  -----
    ## options
    dc  = ppopt['PF_DC']        ## 1 = DC OPF, 0 = AC OPF
    alg = ppopt['OPF_ALG']
    verbose = ppopt['VERBOSE']

    ## build user-defined costs
    om.build_cost_params()

    ## get indexing
    vv, ll, nn, _ = om.get_idx()

    if verbose > 0:
        v = ppver('all')
        stdout.write('\nMATPOWER Version %s, %s' % (v['Version'], v['Date']))

    ##-----  run DC OPF solver  -----
    if dc:
        if verbose > 0:
            stdout.write(' -- DC Optimal Power Flow\n')

        results, success, raw = dcopf_solver(om, ppopt)
    else:
        ##-----  run AC OPF solver  -----
        if verbose > 0:
            stdout.write(' -- AC Optimal Power Flow\n')

        ## if OPF_ALG not set, choose best available option
        if alg == 0:
            alg = 560                ## MIPS

        ## update deprecated algorithm codes to new, generalized formulation equivalents
        if alg == 100 | alg == 200:        ## CONSTR
            alg = 300
        elif alg == 120 | alg == 220:      ## dense LP
            alg = 320
        elif alg == 140 | alg == 240:      ## sparse (relaxed) LP
            alg = 340
        elif alg == 160 | alg == 260:      ## sparse (full) LP
            alg = 360

        ppopt['OPF_ALG_POLY'] = alg

        ## run specific AC OPF solver
        if alg == 560 or alg == 565:                   ## PIPS
            results, success, raw = pipsopf_solver(om, ppopt)
        elif alg == 580:                              ## IPOPT
            try:
                __import__('pyipopt')
                results, success, raw = ipoptopf_solver(om, ppopt)
            except:
                stderr.write('opf_execute: OPF_ALG %d requires IPOPT (see https://projects.coin-or.org/Ipopt/)\n' % alg)
        else:
            stderr.write('opf_execute: OPF_ALG %d is not a valid algorithm code\n' % alg)

    if ('output' not in raw) or ('alg' not in raw['output']) or (len(raw['output']['alg']) == 0):
        raw['output']['alg'] = alg

    if success:
        if not dc:
            ## copy bus voltages back to gen matrix
            results['gen'][:, VG] = results['bus'][results['gen'][:, GEN_BUS].astype(int), VM]

            ## gen PQ capability curve multipliers
            if (ll['N']['PQh'] > 0) | (ll['N']['PQl'] > 0):
                mu_PQh = results['mu']['lin']['l'][ll['i1']['PQh']:ll['iN']['PQh']] - results['mu']['lin']['u'][ll['i1']['PQh']:ll['iN']['PQh']]
                mu_PQl = results['mu']['lin']['l'][ll['i1']['PQl']:ll['iN']['PQl']] - results['mu']['lin']['u'][ll['i1']['PQl']:ll['iN']['PQl']]
                Apqdata = om.userdata('Apqdata')
                results['gen'] = update_mupq(results['baseMVA'], results['gen'], mu_PQh, mu_PQl, Apqdata)

            ## compute g, dg, f, df, d2f if requested by RETURN_RAW_DER = 1
            if ppopt['RETURN_RAW_DER']:
                ## move from results to raw if using v4.0 of MINOPF or TSPOPF
                if 'dg' in results:
                    raw = {}
                    raw['dg'] = results['dg']
                    raw['g'] = results['g']

                ## compute g, dg, unless already done by post-v4.0 MINOPF or TSPOPF
                if 'dg' not in raw:
                    ppc = om.get_ppc()
                    Ybus, Yf, Yt = makeYbus(ppc['baseMVA'], ppc['bus'], ppc['branch'])
                    g, geq, dg, dgeq = opf_consfcn(results['x'], om, Ybus, Yf, Yt, ppopt)
                    raw['g'] = r_[geq, g]
                    raw['dg'] = r_[dgeq.T, dg.T]   ## true Jacobian organization

                ## compute df, d2f
                _, df, d2f = opf_costfcn(results['x'], om, True)
                raw['df'] = df
                raw['d2f'] = d2f

        ## delete g and dg fieldsfrom results if using v4.0 of MINOPF or TSPOPF
        if 'dg' in results:
            del results['dg']
            del results['g']

        ## angle limit constraint multipliers
        if ll['N']['ang'] > 0:
            iang = om.userdata('iang')
            results['branch'][iang, MU_ANGMIN] = results['mu']['lin']['l'][ll['i1']['ang']:ll['iN']['ang']] * pi / 180
            results['branch'][iang, MU_ANGMAX] = results['mu']['lin']['u'][ll['i1']['ang']:ll['iN']['ang']] * pi / 180
    else:
        ## assign empty g, dg, f, df, d2f if requested by RETURN_RAW_DER = 1
        if not dc and ppopt['RETURN_RAW_DER']:
            raw['dg'] = array([])
            raw['g'] = array([])
            raw['df'] = array([])
            raw['d2f'] = array([])

    ## assign values and limit shadow prices for variables
    if om.var['order']:
        results['var'] = {'val': {}, 'mu': {'l': {}, 'u': {}}}
    for name in om.var['order']:
        if om.getN('var', name):
            idx = arange(vv['i1'][name], vv['iN'][name])
            results['var']['val'][name] = results['x'][idx]
            results['var']['mu']['l'][name] = results['mu']['var']['l'][idx]
            results['var']['mu']['u'][name] = results['mu']['var']['u'][idx]

    ## assign shadow prices for linear constraints
    if om.lin['order']:
        results['lin'] = {'mu': {'l': {}, 'u': {}}}
    for name in om.lin['order']:
        if om.getN('lin', name):
            idx = arange(ll['i1'][name], ll['iN'][name])
            results['lin']['mu']['l'][name] = results['mu']['lin']['l'][idx]
            results['lin']['mu']['u'][name] = results['mu']['lin']['u'][idx]

    ## assign shadow prices for nonlinear constraints
    if not dc:
        if om.nln['order']:
            results['nln'] = {'mu': {'l': {}, 'u': {}}}
        for name in om.nln['order']:
            if om.getN('nln', name):
                idx = arange(nn['i1'][name], nn['iN'][name])
                results['nln']['mu']['l'][name] = results['mu']['nln']['l'][idx]
                results['nln']['mu']['u'][name] = results['mu']['nln']['u'][idx]

    ## assign values for components of user cost
    if om.cost['order']:
        results['cost'] = {}
    for name in om.cost['order']:
        if om.getN('cost', name):
            results['cost'][name] = om.compute_cost(results['x'], name)

    ## if single-block PWL costs were converted to POLY, insert dummy y into x
    ## Note: The "y" portion of x will be nonsense, but everything should at
    ##       least be in the expected locations.
    pwl1 = om.userdata('pwl1')
    if (len(pwl1) > 0) and (alg != 545) and (alg != 550):
        ## get indexing
        vv, _, _, _ = om.get_idx()
        if dc:
            nx = vv['iN']['Pg']
        else:
            nx = vv['iN']['Qg']

        y = zeros(len(pwl1))
        raw['xr'] = r_[raw['xr'][:nx], y, raw['xr'][nx:]]
        results['x'] = r_[results['x'][:nx], y, results['x'][nx:]]

    return results, success, raw
