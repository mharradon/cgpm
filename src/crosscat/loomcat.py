# Copyright (c) 2014, Salesforce.com, Inc.  All rights reserved.
# Copyright (c) 2015, Google, Inc.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
# - Redistributions of source code must retain the above copyright
#   notice, this list of conditions and the following disclaimer.
# - Redistributions in binary form must reproduce the above copyright
#   notice, this list of conditions and the following disclaimer in the
#   documentation and/or other materials provided with the distribution.
# - Neither the name of Salesforce.com nor the names of its contributors
#   may be used to endorse or promote products derived from this
#   software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED.  IN NO EVENT SHALL THE
# COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS
# OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR
# TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE
# USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import csv
import itertools
import os

import pandas as pd

import loom.cleanse
import loom.tasks

from distributions.io.stream import open_compressed
from loom.cFormat import assignment_stream_load

from cgpm.mixtures.view import View
from cgpm.utils import config as cu


DEFAULT_LOOM_STORE = os.path.join(os.sep, 'tmp', 'cgpm', 'loomstore')

DEFAULT_CLEANSED_DIR = 'cleansed'
DEFAULT_RAW_DIR = 'raw'
DEFAULT_RESULTS_DIR = 'results'


def _retrieve_loom_store():
    if os.environ.get('CGPM_LOOM_STORE'):
        return os.environ['CGPM_LOOM_STORE']
    if not os.path.exists(DEFAULT_LOOM_STORE):
        os.makedirs(DEFAULT_LOOM_STORE)
    return DEFAULT_LOOM_STORE


def _generate_project_paths():
    # Create a unique project directory in the store.
    store = _retrieve_loom_store()
    timestamp = cu.timestamp()
    project_root = os.path.join(store, timestamp)
    # Create necessary subdirectories.
    paths = {
        'root'      : project_root,
        'raw'       : os.path.join(project_root, DEFAULT_RAW_DIR),
        'cleansed'  : os.path.join(project_root, DEFAULT_CLEANSED_DIR),
        'results'   : os.path.join(project_root, DEFAULT_RESULTS_DIR)

    }
    for path in paths.values():
        if not os.path.exists(path):
            os.makedirs(path)
    return paths


def _generate_column_names(state):
    return [unicode('c%05d') % (i,) for i in state.outputs]


def _generate_loom_stattypes(state):
    cctypes = state.cctypes()
    distargs = state.distargs()
    return [cu.loom_stattype(s, d) for s, d in zip(cctypes, distargs)]


def _write_dataset(state, path):
    frame = pd.DataFrame([state.X[i] for i in state.outputs]).T
    assert frame.shape == (state.n_rows(), state.n_cols())
    frame.columns = _generate_column_names(state)
    # Update columns which can be safely converted to int.
    for col in frame.columns:
        if all(frame[col] == frame[col]//1):
            frame[col] = frame[col].astype(int)
    frame.to_csv(path, na_rep='', index=False)


def _write_schema(state, path):
    column_names = _generate_column_names(state)
    loom_stattypes = _generate_loom_stattypes(state)
    with open(path, 'wb') as schema_file:
        writer = csv.writer(
            schema_file, delimiter=',', quoting=csv.QUOTE_MINIMAL)
        writer.writerow(['Feature Name', 'Type'])
        writer.writerows(zip(column_names, loom_stattypes))


def _loom_initialize(state):
    '''Run the preprocessing pipeline.
    |- write dataset and schema to csv
    |- cleanse
    |- transform
    |- ingest
    |- next: infer
    '''
    paths = _generate_project_paths()

    # Write dataset and schema csv files.
    dataset_file = os.path.join(paths['raw'], 'data.csv')
    schema_file = os.path.join(paths['raw'], 'schema.csv')

    _write_dataset(state, dataset_file)
    _write_schema(state, schema_file)

    # Cleanse and compress the dataset for Loom.
    cleansed_file = os.path.join(paths['cleansed'], 'data.csv.gz')
    loom.cleanse.force_ascii(dataset_file, cleansed_file)

    # Transform the cleansed dataset into Loom.
    loom.tasks.transform(paths['results'], schema_file, paths['cleansed'])

    # Ingest the transformed data.
    loom.tasks.ingest(paths['results'])

    return paths


def _sample_filename(path, sample, filename):
    return os.path.join(path, 'samples', 'sample.%d' % (sample,), filename)


def _loom_cross_cat(path, sample):
    model_in = _sample_filename(path, sample, 'model.pb.gz')
    cross_cat = loom.schema_pb2.CrossCat()
    with open_compressed(model_in, 'rb') as f:
        cross_cat.ParseFromString(f.read())
    return cross_cat


def _retrieve_column_partition(path, sample):
    cross_cat = _loom_cross_cat(path, sample)
    # Zv structure from cgpm.crosscat.State.
    return dict(itertools.chain.from_iterable([
        [(featureid, k) for featureid in kind.featureids]
        for k, kind in enumerate(cross_cat.kinds)
    ]))


def _retrieve_row_partitions(path, sample):
    cross_cat = _loom_cross_cat(path, sample)
    num_kinds = len(cross_cat.kinds)
    assign_in = _sample_filename(path, sample, 'assign.pbs.gz')
    assignments = {
        a.rowid: [a.groupids(k) for k in xrange(num_kinds)]
        for a in assignment_stream_load(assign_in)
    }
    rowids = sorted(assignments)
    # Zrv structure from cgpm.crosscat.State.
    return {
        k: [assignments[rowid][k] for rowid in rowids]
        for k in xrange(num_kinds)
    }


def _update_state(state, path, sample):
    # Retrieve the new view partition from loom.
    Zv_new = _retrieve_column_partition(path, sample)
    Zvr_new = _retrieve_row_partitions(path, sample)
    assert set(Zv_new.values()) == set(Zvr_new.keys())

    # Create new views in cgpm with the Loom row partitions.
    offset = max(state.views) + 1
    new_views = []
    for v in sorted(set(Zvr_new.keys())):
        index = v + offset
        assert index not in state.views
        view = View(
            state.X, outputs=[10**7 + index],
            Zr=Zvr_new[v], rng=state.rng)
        new_views.append(view)
        state._append_view(view, index)

    # Migrate each dim to its new view.
    for i, c in enumerate(state.outputs):
        v_current = state.Zv(c)
        v_new = Zv_new[i] + offset
        state._migrate_dim(v_current, v_new, state.dim_for(c), reassign=True)

    assert len(state.views) == len(new_views)
    state._check_partitions()


# Run some ad-hoc tests.

from cgpm.utils import general as gu
from cgpm.utils import test as tu
from cgpm.crosscat.state import State

# Set up the data generation
cctypes, distargs = cu.parse_distargs([
    'normal',
    'poisson',
    'bernoulli',
    'categorical(k=4)',
    'lognormal',
    'exponential',
    'beta',
    'geometric',
    'vonmises'
])

T, Zv, Zc = tu.gen_data_table(
    10, [1], [[.25, .25, .5]], cctypes, distargs,
    [.95]*len(cctypes), rng=gu.gen_rng(10))

state = State(T.T, cctypes=cctypes, distargs=distargs, rng=gu.gen_rng(312))
state.transition(N=1)

paths = _loom_initialize(state)

loom.tasks.infer(
    paths['results'], sample_count=1,
    config={"schedule": {"extra_passes": 100}})

_update_state(state, paths['results'], 0, )
