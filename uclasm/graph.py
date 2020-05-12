"""Graph class for representing networks with node and edge attributes."""

import scipy.sparse as sparse
import numpy as np
import pandas as pd
import networkx as nx
import sys

from .utils import index_map, one_hot
from .equivalence import bfs_partition_graph

# functools.cached_property was introduced in python 3.8.
# https://docs.python.org/3/library/functools.html#functools.cached_property
if sys.version_info >= (3, 8):
    from functools import cached_property
else:
    from lazy_property import LazyProperty as cached_property


class Graph:
    """An multigraph class with support for node and edge attributes.

    Parameters
    ----------
    adjs : list(spmatrix)
        Adjacency matrices counting edges in each channel.
    channels : list(str), optional
        Types of edge, one per adjacency matrix.
    nodelist : DataFrame, optional
        Attributes of each node.
    edgelist : DataFrame, optional
        Attributes of each edge.

    Attributes
    ----------
    n_nodes : int
        Number of nodes in the graph.
    n_channels : int
        Number of types of edge in the graph.
    channels : list(str)
        Types of edge present in the graph.
    adjs : list(spmatrix)
        Adjacency matrices corresponding to each channel.
    ch_to_adj : dict(str, spmatrix)
        Map from channels to their corresponding adjacency matrix
    nodes : Series
        A Series containing node identifiers. These are particularly
        useful for keeping track of nodes when taking subgraphs.
    nodelist : DataFrame
        A DataFrame containing node attribute information.
    edgelist : DataFrame, optional
        A DataFrame containing edge attribute information.
    node_col : str
        Column name for node identifiers in the nodelist.
    source_col : str
        Column name for source node identifiers in the edgelist.
    target_col : str
        Column name for target node identifiers in the edgelist.
    channel_col : str
        Column name for edge type/channel identifiers in the edgelist.
    """

    node_col = "Node"
    source_col = "Source"
    target_col = "Target"
    channel_col = "eType"

    def __init__(self, adjs, channels=None, nodelist=None, edgelist=None):
        """Derive attributes from parameters."""
        # Reverse compatibility with old format
        if channels is not None and len(adjs) != len(channels):
            # Old order is nodelist, channels, adjs
            temp = nodelist
            nodelist = pd.DataFrame(adjs, columns=[Graph.node_col])
            adjs = temp
        if channels is not None and len(adjs) != len(channels):
            raise Exception("Unable to match adjs to channels")

        self.n_nodes = adjs[0].shape[0]
        self.n_channels = len(adjs)

        if channels is None:
            # e.g. ["channel 0", "channel 1", ...]
            channels = ["channel {}".format(i) for i in range(self.n_channels)]

        self.channels = list(channels)
        self.adjs = list(adjs)
        self.ch_to_adj = dict(zip(self.channels, self.adjs))

        # TODO: If an edgelist was supplied, use it to compute this stuff.

        # If a nodelist is not supplied, generate a basic one
        if nodelist is None:
            # e.g. ["node 0", "node 1", ...]
            node_names = ["node {}".format(i) for i in range(self.n_nodes)]
            nodelist = pd.DataFrame(node_names, columns=[self.node_col])

        self.nodelist = nodelist
        self.nodes = self.nodelist[self.node_col]
        self.node_idxs = index_map(self.nodes)

        # TODO: Make sure nodelist is indexed in a reasonable way
        # TODO: Make sure edgelist is indexed in a reasonable way
        # TODO: Check dtypes of edgelist and nodelist columns.

        self.edgelist = edgelist

    def copy(self):
        """Returns a copy of the graph.
        Returns
        -------
        Graph
            A copy of the graph.
        """
        adjs_copy = [adj.copy() for adj in self.adjs]
        edgelist_copy = None
        if self.edgelist is not None:
            edgelist_copy = self.edgelist.copy()
        return Graph(adjs_copy, channels=self.channels,
                     nodelist=self.nodelist.copy(),
                     edgelist=edgelist_copy)

    @cached_property
    def has_loops(self):
        """bool: Indicator of whether the graph has any self-edges."""
        # Generates the number of nonzeros in each channel.
        nnz_gen = (len(adj.diagonal().nonzero()[0]) for adj in self.adjs)

        return any(nnz_gen)

    @cached_property
    def composite_adj(self):
        """spmatrix: Composite adjacency matrix of the graph.

        Each entry of this matrix corresponds to the total number of edges
        of any type going from the node corresponding to the row to the node
        corresponding to the column.
        """
        return sum(self.adjs)

    @cached_property
    def sym_composite_adj(self):
        """spmatrix: Symmetrized composite adjacency matrix of the graph.

        Each entry of this matrix corresponds to the total number of edges
        of any type between the pair of nodes indicated by the row and column
        indices, ignoring the direction of the edges.
        """
        return self.composite_adj + self.composite_adj.T

    @cached_property
    def is_nbr(self):
        """spmatrix: Boolean adjacency matrix of the graph.

        Each entry of this matrix indicates whether the pair of nodes
        corresponding to the row and column indices are connected by an edge in
        either direction in any channel. The entry will be True if the nodes
        are connected by an edge in some channel and False otherwise.
        """
        return self.sym_composite_adj > 0

    @cached_property
    def nbr_idx_pairs(self):
        """2darray: A [N, 2] array of adjacent pairs of node indices.

        A 2d array with 2 columns. Each row contains the indices of a pair of
        neighboring nodes in the graph. Each pair is only returned once, so
        only one of (i, j) and (j, i) can appear as rows.
        """
        return np.argwhere(sparse.tril(self.is_nbr))

    @cached_property
    def self_edges(self):
        """2darray: An array of self-edge counts in each channel.

        A 2darray of shape [n_nodes, n_channels]. Each entry provides the
        number of self edges of the node corresponding to the row in the
        channel corresponding to the channel.
        """
        return np.stack([adj.diagonal() for adj in self.adjs], axis=1)

    @cached_property
    def in_degrees(self):
        """2darray: An array of in degrees in each channel.

        A 2darray of shape [n_nodes, n_channels]. Each entry provides the
        in-degree of the node corresponding to the row in the channel
        corresponding to the channel.
        """
        in_degrees_list = [adj.sum(axis=0).T.A for adj in self.adjs]
        in_degrees_array = np.concatenate(in_degrees_list, axis=1)
        return in_degrees_array - self.self_edges

    @cached_property
    def out_degrees(self):
        """2darray: An array of out degrees in each channel.

        A 2darray of shape [n_nodes, n_channels]. Each entry provides the
        out-degree of the node corresponding to the row in the channel
        corresponding to the channel.
        """
        out_degrees_list = [adj.sum(axis=1).A for adj in self.adjs]
        out_degrees_array = np.concatenate(out_degrees_list, axis=1)
        return out_degrees_array - self.self_edges

    @cached_property
    def in_out_degrees(self):
        """2darray: An array of in and out degrees in each channel.

        A 2darray of shape [n_nodes, 2 * n_channels]. The first n_channels
        entries of each row are the in-degrees of the nodes corresponding to
        the row in each channel. The remaining n_channels entries of each row
        are the out-degrees.
        """
        deglist = [self.in_degrees, self.out_degrees]
        return np.concatenate(deglist, axis=1).astype(np.single)

    @cached_property
    def equivalence_classes(self):
        """Equivalence: The equivalence structure of the graph
        
        Computes the template equivalence 
        """
        return bfs_partition_graph(self)

    def loopless_subgraph(self):
        """Get a subgraph containing no self-edges.

        Returns
        -------
        Graph
            A graph with the same nodes and edges as self, omitting self-edges.
        """
        adjs = [adj.copy() for adj in self.adjs]
        for adj in adjs:
            # manually zero only the non-zero diagonal elements
            nonzero, = adj.diagonal().nonzero()
            adj[nonzero, nonzero] = 0

        if self.edgelist is not None:
            # TODO: Test this to see if it works with dask DataFrames.
            _sources = self.edgelist[self.source_col]
            _targets = self.edgelist[self.target_col]
            edgelist = self.edgelist[_sources != _targets].reset_index(drop=True)
        else:
            edgelist = None

        # Return a new graph object for the subgraph
        return Graph(adjs, self.channels, self.nodelist, edgelist)

    def node_subgraph(self, node_idxs):
        """Get the subgraph induced by the specified node indices.

        TODO: Any of the composite adjacency matrices should be subgraphed if
        they have been computed.

        Parameters
        ----------
        node_idxs : 1darray
            The indices corresponding to the nodes in the desired subgraph.

        Returns
        -------
        Graph
            The induced subgraph.
        """
        # throw out nodes not belonging to the desired subgraph
        adjs = [adj[node_idxs, :][:, node_idxs] for adj in self.adjs]
        nodelist = self.nodelist.iloc[node_idxs].reset_index(drop=True)
        nodes = nodelist[self.node_col]

        if self.edgelist is not None:
            # TODO: Test this to see if it works with dask DataFrames.
            _sources = self.edgelist[self.source_col].isin(nodes)
            _targets = self.edgelist[self.target_col].isin(nodes)
            edgelist = self.edgelist[_sources & _targets].reset_index(drop=True)
        else:
            edgelist = None

        # Return a new graph object for the induced subgraph
        return Graph(adjs, self.channels, nodelist, edgelist)

    def channel_subgraph(self, channels):
        """Get the subgraph induced by the specified channels.

        Parameters
        ----------
        channels : 1darray
            The desired channels to keep in the subgraph.

        Returns
        -------
        Graph
            The induced subgraph.
        """
        # throw out nodes not belonging to the desired subgraph
        adjs = [self.adjs[self.channels.index(ch)] for ch in channels]

        if self.edgelist is not None:
            # Drop edges that do not have types among the desired channels.
            edge_ind = self.edgelist[self.channel_col].isin(channels)
            edgelist = self.edgelist[edge_ind].reset_index(drop=True)
        else:
            edgelist = None

        # Return a new graph object for the induced subgraph
        return Graph(adjs, channels, self.nodelist, edgelist)

    def node_cover(self):
        """Get the indices of nodes for a node cover, sorted by importance.

        This function provides no warranty of the optimality of the node cover.
        The computed node cover may be far from the smallest possible.

        Returns
        -------
        1darray
            The indices of a set of nodes in a node cover.
        """
        cover = []

        # Initially there are no nodes in the cover. Thus all of the nodes in
        # the graph are uncovered.
        uncov = np.ones(self.n_nodes, dtype=np.bool_)

        # Until the cover disconnects the graph, add a node to the cover
        while self.is_nbr[uncov, :][:, uncov].nnz:

            # Add the uncov node with the most neighbors
            nbr_counts = np.sum(self.is_nbr[uncov, :][:, uncov], axis=0)

            imax = np.argmax(nbr_counts)
            cover.append(imax)

            # Cover the node corresponding to imax.
            uncov[uncov] = ~one_hot(imax, np.sum(uncov))

        # TODO: Remove any nodes from the node cover which are not necessary.

        return np.array(cover)


def from_networkx_graph(nx_graph):
    """
    This will convert a networkx style graph into a uclasm style Graph.
    Currently this does not preserve node or edge labels. It just copies
    over the adjacency structure.

    Parameters
    ----------
    nx_graph : networkx.Graph
    """

    # TODO: Add the ability to port over node and edge labels
    adj = nx.to_scipy_sparse_matrix(nx_graph)
    return Graph([adj])
