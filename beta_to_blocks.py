#!/usr/bin/python3 -u

import argparse
import os
import numpy as np
import os.path as op
import pandas as pd
from utils_wgbs import validate_files_list
import multiprocessing
from multiprocessing import Pool
import sys
from utils_wgbs import load_beta_data, trim_to_uint8, default_blocks_path, GenomeRefPaths, \
                        IllegalArgumentError, add_multi_thread_args


def b2b_log(*args, **kwargs):
    print('[ wt beta_to_blocks ]', *args, file=sys.stderr, **kwargs)

######################################################
#                                                    #
#  Loading and Parsing Blocks file                   #
#                                                    #
######################################################

def is_block_file_nice(df):

    # startCpG and endCpG is monotonically increasing
    if not pd.Index(df['startCpG']).is_monotonic:
        b2b_log('startCpG is not monotonically increasing')
        return False
    if not pd.Index(df['endCpG']).is_monotonic:
        b2b_log('endCpG is not monotonically increasing')
        return False

    # no duplicated blocks
    if (df.shape[0] != df.drop_duplicates().shape[0]):
        b2b_log('Some blocks are duplicated')
        return False

    # no empty blocks
    if not (df['endCpG'] - df['startCpG'] > 0).all():
        b2b_log('Some blocks are empty (no CpGs)')
        return False

    # no overlaps between blocks
    if not (df['startCpG'][1:].values - df['endCpG'][:df.shape[0] - 1].values  >= 0).all():
        b2b_log('Some blocks overlap')
        return False

    return True


def load_blocks_file(blocks_path):
    if not op.isfile(blocks_path):
        b2b_log('Invalid blocks file:', blocks_path)
        raise IllegalArgumentError('No blocks file')

    names = ['chr', 'start', 'end', 'startCpG', 'endCpG']
    df = pd.read_csv(blocks_path, sep='\t', usecols=range(5), header=None, names=names)

    # blocks start before they end - invalid file
    if not ((df['endCpG'] -  df['startCpG']) >= 0).all():
        raise IllegalArgumentError('Invalid blocks file')

    return df


######################################################
#                                                    #
#    Perform the reduction                           #
#                                                    #
######################################################

def fast_method(data, df):
    block_bins = np.unique(np.concatenate([df['startCpG'], df['endCpG'], [1, data.shape[0] + 1]]))
    block_bins.sort()
    filtered_indices = np.isin(block_bins, np.concatenate([df['startCpG'], [df['endCpG'].iloc[-1]]]))

    # reduce to blocks:
    block_bins[-1] -= 1
    reduced_data = np.add.reduceat(data, block_bins - 1)[filtered_indices][:-1]
    return reduced_data


def slow_method(data, df):
    reduced_data = np.zeros((df.shape[0], 2), dtype=int)
    for i, row in df.iterrows():
        startCpG = row[3]
        endCpG = row[4]
        reduced_data[i, :] = np.sum(data[startCpG - 1:endCpG - 1, :], axis=0)
    return reduced_data



def collapse_process(beta_path, df, is_nice, lbeta, out_dir, bedGraph):
    try:
        # load beta file:
        data = load_beta_data(beta_path)
        if is_nice:
            reduced_data = fast_method(data, df)
        else:
            reduced_data = slow_method(data, df)

        dump(df, reduced_data, beta_path, lbeta, out_dir, bedGraph)

    except Exception as e:
        print('Failed with beta', beta_path)
        print('Exception:', e)


######################################################
#                                                    #
#    Dump results                                    #
#                                                    #
######################################################

def dump(df, reduced_data, beta_path, lbeta, out_dir, bedGraph):

    # dump to binary file
    suff = '.lbeta' if lbeta else '.bin'
    prefix = op.join(out_dir, op.splitext(op.basename(beta_path))[0])
    trim_to_uint8(reduced_data, lbeta).tofile(prefix + suff)
    b2b_log(prefix + suff)

    # dump to bed
    if bedGraph:
        with np.errstate(divide='ignore', invalid='ignore'):
            df['beta'] = reduced_data[:, 0] / reduced_data[:, 1]
        df['coverage'] = reduced_data[:, 1]
        df[['chr', 'start', 'end', 'beta', 'coverage']].to_csv(prefix + '.bedGraph', sep='\t',
                  index=None, header=None, na_rep=-1, float_format='%.2f')

def main():
    """
    Collapse beta file to blocks binary file, of the same beta format
    """

    args = parse_args()
    files = args.input_files
    validate_files_list(files, '.beta')

    # load blocks:
    # b2b_log('load blocks...')
    df = load_blocks_file(args.blocks_file)
    is_nice = is_block_file_nice(df)
    p = Pool(args.threads)
    params = [(b, df, is_nice, args.lbeta, args.out_dir, args.bedGraph)
              for b in files]
    arr = p.starmap(collapse_process, params)
    p.close()
    p.join()

def parse_args():
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument('input_files', nargs='+', help='one or more beta files')
    parser.add_argument('-b', '--blocks_file', help='blocks path', default=default_blocks_path)
    parser.add_argument('-o', '--out_dir', help='output directory. Default is "."', default='.')
    parser.add_argument('-l', '--lbeta', action='store_true', help='Use lbeta file (uint16) instead of bin (uint8)')
    parser.add_argument('--bedGraph', action='store_true', help='output a text file in addition to binary file')
    parser.add_argument('--debug', '-d', action='store_true')
    parser.add_argument('--genome', help='Genome reference name. Default is hg19.', default='hg19')
    add_multi_thread_args(parser)

    return parser.parse_args()


if __name__ == '__main__':
    main()
