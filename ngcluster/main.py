import os
import sys
import datetime

import numpy as np
import matplotlib.pyplot as plt

from ngcluster.config import configurations, external_cluster_files
from ngcluster.graph import count_edges
from ngcluster.evaluate import (ClusterEvaluationError, aggregate_fom,
        rand_index, silhouette_widths, silhouette_stats)
from ngcluster.plot import plot_cluster_expression, save_pdf

def main(datadir, outdir, run_configs):
    """
    Run the main program.

    Parameters
    ----------
    datadir : string
        The path to the directory containing the input data files yeastEx.txt
        and yeastNames.txt.

    outdir : string
        The path to the top-level directory to which the output files will be
        saved. The output for each configuration will be stored in a
        subdirectory named with that configuration's key.

    run_configs : list of string
        A list of keys of the configurations to run (see ngcluster.config).
    """

    logfile = None
    def log(msg):
        print(msg)
        print(msg, file=logfile)

    data = np.loadtxt(os.path.join(datadir, 'yeastEx.txt'))
    names = np.loadtxt(os.path.join(datadir, 'yeastNames.txt'),
            dtype=bytes).astype(str)

    if run_configs == []:
        print("Usage:\n"
              "    python3 run.py <config1> [<config2> ...]\n"
              "  or\n"
              "    python3 run.py all\n"
              "  to run all configurations.\n"
              "Available configurations (see ngcluster/config.py):")
        for key, config in configurations.items():
            print("  {0}: {1}".format(key, config['description']))
        sys.exit(1)

    elif run_configs == ['all']:
        run_configs = list(configurations.keys())
        print("Running all {0} configurations: {1}"
                .format(len(run_configs), ", ".join(run_configs)))
    else:
        for key in run_configs:
            if key not in configurations:
                print("Error: '{0}' is not a valid configuration".format(key))
                sys.exit(1)

    external_clusterings = [
            (filename,
                load_external_clusters(names, os.path.join(datadir, filename)))
            for filename in external_cluster_files]

    for key in run_configs:
        config = configurations[key]
        config_outdir = os.path.join(outdir, key)
        os.makedirs(config_outdir, exist_ok=True)
        logfile = open(os.path.join(config_outdir, key + '-log.txt'), 'w')

        print("===============================================================")
        log(datetime.datetime.now().strftime('%c'))
        log("Running configuration " + key)
        log(str(config))

        cluster_fn, cluster_kwargs = config['cluster']
        graph_fn, graph_kwargs = config.get('graph', (None, None))

        log("Calculating aggregate FOM")
        try:
            fom = aggregate_fom(data,
                    graph_fn, graph_kwargs, cluster_fn, cluster_kwargs)
            log("Aggregate FOM = {0}".format(fom))
            pass
        except ClusterEvaluationError as e:
            log("Cannot calculate aggregate FOM: {0}".format(e))

        log("Clustering entire dataset")
        if graph_fn is None:
            # Do non-graph-based clustering
            log("Computing clusters")
            clusters = cluster_fn(data, **cluster_kwargs)
        else:
            # Do graph-based clustering
            log("Computing graph")
            adj = graph_fn(data, **graph_kwargs)
            log("Edges-to-nodes ratio = {}".format(
                float(count_edges(adj)) / data.shape[0]))
            log("Computing clusters")
            clusters = cluster_fn(adj, **cluster_kwargs)

        num_clusters = clusters.max() + 1
        log("{0} clusters generated".format(num_clusters))
        if num_clusters <= 0:
            log("Error: There are no clusters. Skipping configuration")
            continue
        total_genes = len(data)
        clustered_genes = (clusters >= 0).sum()
        log("{0} of {1} genes clustered ({2}%)"
                .format(clustered_genes, total_genes,
                    round(100 * float(clustered_genes) / total_genes)))

        clusters_outdata = np.vstack((names, clusters)).transpose()
        np.savetxt(os.path.join(config_outdir, key + '-clusters.txt'),
                clusters_outdata, fmt='%s')

        log("\nSilhouette statistics:")
        log("{:11} {:>13} {:>9} {:>9}".format(
            "metric", "weighted_mean", "min",  "max"))
        for metric in 'euclidean', 'correlation', 'cosine':
            widths = silhouette_widths(clusters, data, metric)
            stats, summary = silhouette_stats(clusters, widths)
            log("{:11} {:13.3f} {:9.3f} {:9.3f}".format(metric,
                summary['weighted_mean'], summary['min'], summary['max']))

            np.savetxt(
                    os.path.join(
                        config_outdir,
                        "{0}-silhouette-{1}.txt".format(key, metric)),
                    stats,
                    header=' '.join(stats.dtype.names),
                    fmt="%d %3d %6.3f %6.3f %6.3f",
                    comments='')

        log("\nCluster size:")
        log("{:>8} {:>8} {:>8}".format("mean", "min", "max"))
        log("{:8.2f} {:8d} {:8d}".format(
            stats['count'].mean(), stats['count'].min(), stats['count'].max()))
        log('')

        for ext_filename, ext_clusters in external_clusterings:

            # Only consider genes that are clustered in both clusterings
            ext_clusters = ext_clusters.copy()
            ext_clusters[clusters < 0] = -1
            int_clusters = clusters.copy()
            int_clusters[ext_clusters < 0] = -1

            rand_index_val = rand_index(int_clusters, ext_clusters)
            log("Rand index = {0} ({1})".format(rand_index_val, ext_filename))

        log("Plotting cluster expression levels")
        figs = plot_cluster_expression(names, data, clusters)
        #save_pdf(figs, os.path.join(config_outdir, key + '-figures.pdf'))
        for i, fig in enumerate(figs):
            fig.savefig(os.path.join(config_outdir, key + '-cluster-{0}.png'
                .format(i)))
        plt.close('all')

        log("Finished running configuration {0}".format(key))
        log(datetime.datetime.now().strftime('%c'))
        print()
        logfile.close()

def load_external_clusters(names, filename):
    """
    Load cluster assignments from an external file.

    Parameters
    ----------
    names : ndarray
        The array of gene names.

    filename : string
        The full path to the file to load. The file should be a text file with
        two columns delimited by whitespace. The first column should contain the
        names of the clustered genes, and the second column should contain
        integer cluster IDs or arbitrary cluster labels. It is an error to mix
        the two. In the case of integer IDs, a negative value indicates that the
        corresponding gene is not in a cluster.

    Returns
    -------
    ndarray
        An array of cluster assignments in the same format as returned by the
        functions in ngcluster.cluster.
    """

    clusters = np.empty(len(names), dtype=int)
    clusters.fill(-1)

    # Map gene names to their positions in the data
    gene_id_lookup = {name: i for i, name in enumerate(names)}

    # Given a cluster label or cluster ID, return an integer cluster ID
    label_type = None
    cluster_id_lookup = {}
    next_cluster_id = 0
    def get_cluster_id(label):
        nonlocal label_type, cluster_id_lookup, next_cluster_id
        if label_type is None:
            try:
                cluster_id = int(label)
                label_type = int
            except ValueError:
                label_type = str
        if label_type is int:
            cluster_id = int(label)
        elif label not in cluster_id_lookup:
            cluster_id = next_cluster_id
            cluster_id_lookup[label] = cluster_id
            next_cluster_id += 1
        else:
            cluster_id = cluster_id_lookup[label]

        return cluster_id

    with open(filename, 'r') as f:
        for line in f:
            gene_name, cluster_label = line.split(maxsplit=1)
            if gene_name not in names:
                continue
            clusters[gene_id_lookup[gene_name]] = get_cluster_id(cluster_label)

    return clusters
