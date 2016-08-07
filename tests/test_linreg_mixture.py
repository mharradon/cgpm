# -*- coding: utf-8 -*-

# Copyright (c) 2015-2016 MIT Probabilistic Computing Project

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#    http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import matplotlib.cm as cm
import matplotlib.pyplot as plt
import numpy as np

from cgpm.crosscat.state import State
from cgpm.utils import general as gu


def _compute_y(x):
    noise = [.5, 1]
    slopes = [-2, 5]
    model = x > 5
    return slopes[model] * x  + rng.normal(scale=noise[model])


rng = gu.gen_rng(1)
X = rng.uniform(low=0, high=10, size=50)
Y = map(_compute_y, X)
D = np.column_stack((X,Y))


def replace_key(d, a, b):
    d[b] = d[a]
    del d[a]
    return d


def generate_gaussian_samples():
    state = State(
        D, cctypes=['normal','normal'], Zv={0:0, 1:0}, rng=gu.gen_rng(0))
    view = state.view_for(1)
    state.transition(S=15, kernels=['rows','column_params','column_hypers'])
    samples = view.simulate(-1, [0,1, view.outputs[0]], N=100)
    return [replace_key(s, view.outputs[0], -1) for s in samples]


def generate_regression_samples():
    state = State(
        D, cctypes=['normal','normal'], Zv={0:0, 1:0}, rng=gu.gen_rng(4))
    view = state.view_for(1)
    state.update_cctype(1, 'linear_regression')
    state.transition(S=30, kernels=['rows','column_params','column_hypers'])
    samples = view.simulate(-1, [0, 1, view.outputs[0]], N=100)
    return [replace_key(s, view.outputs[0], -1) for s in samples]

def plot_samples(samples, title):
    fig, ax = plt.subplots()
    clusters = set(s[-1] for s in samples)
    colors = iter(cm.Set1(np.linspace(0, 1, len(clusters)+2)))
    for i, c in enumerate(clusters):
        sc = [(j[0], j[1]) for j in samples if j[-1] == c]
        xs, ys = zip(*sc)
        ax.scatter(
            xs, ys, alpha=.5, color=next(colors),
            label='Cluster %d' %i)
    ax.set_title(title, fontweight='bold', fontsize=16)
    ax.scatter(D[:,0], D[:,1], color='k', alpha=.6, label='Observed Data')
    # ax.legend(framealpha=0, loc='upper left', prop={'weight':'bold'})
    fig.set_size_inches(1.5,1)
    ax.set_xlim([-2, 14])
    ax.set_ylim([-20, 70])
    ax.grid()
    return fig

# def test_regression_plot_crash__ci_():
samples_a = generate_gaussian_samples()
samples_b = generate_regression_samples()
fig_a = plot_samples(samples_a, '')
fig_b = plot_samples(samples_b, '')

def save_figure(f, name):
    f.set_size_inches(5, 3)
    f.set_tight_layout(True)
    f.savefig(name)

# plt.close('all')
