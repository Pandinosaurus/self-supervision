from __future__ import division, print_function, absolute_import
import tensorflow as tf
import functools
import numpy as np
from selfsup.util import DummyDict
from selfsup import ops, caffe
from selfsup.moving_averages import ExponentialMovingAverageExtended
import sys


def _pretrained_vgg_conv_weights_initializer(name, data, info=None, pre_adjust_batch_norm=False, prefix=''):
    shape = None
    if name in data and '0' in data[name]:
        W = data[name]['0'].copy()
        if W.ndim == 2 and name == 'fc6':
            W = W.reshape((W.shape[0], -1, 7, 7))
        elif W.ndim == 2 and name == 'fc7':
            W = W.reshape((W.shape[0], -1, 1, 1))
        elif W.ndim == 2 and name == 'fc8':
            W = W.reshape((W.shape[0], -1, 1, 1))
        W = W.transpose(2, 3, 1, 0)
        init_type = 'file'
        if name == 'conv1_1' and W.shape[2] == 3:
            W = W[:, :, ::-1]
            init_type += ':bgr-flipped'
        bn_name = 'batch_' + name
        if pre_adjust_batch_norm and bn_name in data:
            bn_data = data[bn_name]
            sigma = np.sqrt(1e-5 + bn_data['1'] / bn_data['2'])
            W /= sigma
            init_type += ':batch-adjusted'
        init = tf.constant_initializer(W)
        shape = W.shape
    else:
        init_type = 'init'
        init = tf.contrib.layers.variance_scaling_initializer()
    if info is not None:
        info[prefix + ':' + name + '/weights'] = init_type
    return init, shape


def _pretrained_vgg_inner_weights_initializer(name, data, info=DummyDict(), pre_adjust_batch_norm=False, prefix=''):
    shape = None
    if name in data and '0' in data[name]:
        W = data[name]['0']
        if name == 'fc6':
            W = W.reshape(W.shape[0], 512, 7, 7).transpose(0, 2, 3, 1).reshape(4096, -1).T
        else:
            W = W.T
        init_type = 'file'
        bn_name = 'batch_' + name
        if pre_adjust_batch_norm and bn_name in data:
            bn_data = data[bn_name]
            sigma = np.sqrt(1e-5 + bn_data['1'] / bn_data['2'])
            W /= sigma
            init_type += ':batch-adjusted'
        init = tf.constant_initializer(W.copy())
        shape = W.shape
    else:
        init_type = 'init'
        init = tf.contrib.layers.variance_scaling_initializer()
    info[prefix + ':' + name + '/weights'] = init_type
    return init, shape


def _pretrained_vgg_biases_initializer(name, data, info=DummyDict(), pre_adjust_batch_norm=False, prefix=''):
    shape = None
    if name in data and '1' in data[name]:
        init_type = 'file'
        bias = data[name]['1'].copy()
        bn_name = 'batch_' + name
        if pre_adjust_batch_norm and bn_name in data:
            bn_data = data[bn_name]
            sigma = np.sqrt(1e-5 + bn_data['1'] / bn_data['2'])
            mu = bn_data['0'] / bn_data['2']
            bias = (bias - mu) / sigma
            init_type += ':batch-adjusted'
        init = tf.constant_initializer(bias)
        shape = bias.shape
    else:
        init_type = 'init'
        init = tf.constant_initializer(0.0)
    info[prefix + ':' + name + '/biases'] = init_type
    return init, shape


def _pretrained_vgg_conv_weights(name, data, info=None, pre_adjust_batch_norm=False):
    shape = None
    if name in data and '0' in data[name]:
        W = data[name]['0'].copy()
        if W.ndim == 2 and name == 'fc6':
            W = W.reshape((W.shape[0], -1, 7, 7))
        elif W.ndim == 2 and name == 'fc7':
            W = W.reshape((W.shape[0], -1, 1, 1))
        elif W.ndim == 2 and name == 'fc8':
            W = W.reshape((W.shape[0], -1, 1, 1))
        W = W.transpose(2, 3, 1, 0)
        init_type = 'file'
        if name == 'conv1_1' and W.shape[2] == 3:
            W = W[:, :, ::-1]
            init_type += ':bgr-flipped'
        bn_name = 'batch_' + name
        if pre_adjust_batch_norm and bn_name in data:
            bn_data = data[bn_name]
            sigma = np.sqrt(1e-5 + bn_data['1'] / bn_data['2'])
            W /= sigma
            init_type += ':batch-adjusted'
    else:
        init_type = 'init'
        W = None
    return W


def _pretrained_vgg_biases(name, data, info=DummyDict(), pre_adjust_batch_norm=False):
    shape = None
    if name in data and '1' in data[name]:
        init_type = 'file'
        bias = data[name]['1'].copy()
        bn_name = 'batch_' + name
        if pre_adjust_batch_norm and bn_name in data:
            bn_data = data[bn_name]
            sigma = np.sqrt(1e-5 + bn_data['1'] / bn_data['2'])
            mu = bn_data['0'] / bn_data['2']
            bias = (bias - mu) / sigma
            init_type += ':batch-adjusted'
        shape = bias.shape
    else:
        init_type = 'init'
        bias = 0.0
    return bias


def vgg_conv(x, channels, size=3, padding='SAME', stride=1, hole=1, batch_norm=False,
         phase_test=None, activation=tf.nn.relu, name=None,
         parameter_name=None, summarize_scale=False, info=DummyDict(), parameters={},
         pre_adjust_batch_norm=False, edge_bias_fix=False, previous=None, prefix='',
         use_bias=True, scope=None, global_step=None, squeeze=False):
    if parameter_name is None:
        parameter_name = name
    if scope is None:
        scope = name

    def maybe_squeeze(z):
        if squeeze:
            return tf.squeeze(z, [1, 2])
        else:
            return z

    with tf.name_scope(name):
        features = int(x.get_shape()[3])
        f = channels
        shape = [size, size, features, f]

        W_init, W_shape = _pretrained_vgg_conv_weights_initializer(parameter_name, parameters,
                                                          info=info.get('init'),
                                                          pre_adjust_batch_norm=pre_adjust_batch_norm,
                                                          prefix=prefix)
        b_init, b_shape = _pretrained_vgg_biases_initializer(parameter_name, parameters,
                                                    info=info.get('init'),
                                                    pre_adjust_batch_norm=pre_adjust_batch_norm,
                                                    prefix=prefix)

        assert W_shape is None or tuple(W_shape) == tuple(shape), "Incorrect weights shape for {} (file: {}, spec: {})".format(name, W_shape, shape)
        assert b_shape is None or tuple(b_shape) == (f,), "Incorrect bias shape for {} (file: {}, spec; {})".format(name, b_shape, (f,))

        #import ipdb; ipdb.set_trace()
        with tf.variable_scope(scope):
            W = tf.get_variable('weights', shape, dtype=tf.float32,
                                initializer=W_init)
            b = tf.get_variable('biases', [f], dtype=tf.float32,
                                initializer=b_init)

        if hole == 1:
            conv0 = tf.nn.conv2d(x, W, strides=[1, stride, stride, 1], padding=padding)
        else:
            assert stride == 1
            conv0 = tf.nn.atrous_conv2d(x, W, rate=hole, padding=padding)

        #h1 = tf.nn.bias_add(conv0, b)
        if use_bias:
            h1 = tf.nn.bias_add(conv0, b)
        else:
            h1 = conv0

        if batch_norm:
            assert phase_test is not None, "phase_test required for batch norm"
            mm, vv = tf.nn.moments(h1, [0, 1, 2], name='mommy')
            beta = tf.Variable(tf.constant(0.0, shape=[f]), name='beta', trainable=True)
            gamma = tf.Variable(tf.constant(1.0, shape=[f]), name='gamma', trainable=True)
            #ema = tf.train.ExponentialMovingAverage(decay=0.999)
            ema = ExponentialMovingAverageExtended(decay=0.999, value=[0.0, 1.0],
                    num_updates=global_step)

            def mean_var_train():
                ema_apply_op = ema.apply([mm, vv])
                with tf.control_dependencies([ema_apply_op]):
                    return tf.identity(ema.average(mm)), tf.identity(ema.average(vv))
                    #return tf.identity(mm), tf.identity(vv)

            def mean_var_test():
                return ema.average(mm), ema.average(vv)

            mean, var = tf.cond(~phase_test,
                                mean_var_train,
                                mean_var_test)

            h2 = tf.nn.batch_normalization(h1, mean, var, beta, gamma, 1e-3)
            z = h2
        else:
            z = h1

        if info['config'].get('save_pre'):
            info['activations']['pre:' + name] = maybe_squeeze(z)

        if activation is not None:
            z = activation(z)

    if info.get('scale_summary'):
        with tf.name_scope('activation'):
            tf.summary.scalar('activation/' + name, tf.sqrt(tf.reduce_mean(z**2)))

    info['activations'][name] = maybe_squeeze(z)
    if 'weights' in info:
        info['weights'][name + ':weights'] = W
        info['weights'][name + ':biases'] = b
    return z

#if summarize_scale:
#with tf.name_scope('summaries'):
#tf.scalar_summary('act_' + name, tf.sqrt(tf.reduce_mean(h**2)))
#

def vgg_inner(x, channels, info=DummyDict(), stddev=None,
              activation=tf.nn.relu, name=None, parameters={},
              parameter_name=None, prefix=''):
    if parameter_name is None:
        parameter_name = name
    with tf.name_scope(name):
        f = channels
        features = np.prod(x.get_shape().as_list()[1:])
        xflat = tf.reshape(x, [-1, features])
        shape = [features, channels]

        W_init, W_shape = _pretrained_vgg_inner_weights_initializer(parameter_name, parameters, info=info.get('init'), prefix=prefix)
        b_init, b_shape = _pretrained_vgg_biases_initializer(parameter_name, parameters, info=info.get('init'), prefix=prefix)

        assert W_shape is None or tuple(W_shape) == tuple(shape), "Incorrect weights shape for %s" % name
        assert b_shape is None or tuple(b_shape) == (f,), "Incorrect bias shape for %s" % name

        with tf.variable_scope(name):
            W = tf.get_variable('weights', shape, dtype=tf.float32,
                                initializer=W_init)
            b = tf.get_variable('biases', [f], dtype=tf.float32,
                                initializer=b_init)

        z = tf.nn.bias_add(tf.matmul(xflat, W), b)

    if info['config'].get('save_pre'):
        info['activations']['pre:' + name] = z

    if activation is not None:
        z = activation(z)
    info['activations'][name] = z

    if info.get('scale_summary'):
        with tf.name_scope('activation'):
            tf.summary.scalar('activation/' + name, tf.sqrt(tf.reduce_mean(z**2)))

    if 'weights' in info:
        info['weights'][name + ':weights'] = W
        info['weights'][name + ':biases'] = b
    return z


def build_network(x, info=DummyDict(), parameters={}, hole=1,
                  phase_test=None, convolutional=False, final_layer=True,
                  batch_norm=False,
                  squeezed=False,
                  pre_adjust_batch_norm=False,
                  prefix='', num_features_mult=1.0, use_dropout=True,
                  activation=tf.nn.relu, limit=np.inf,
                  global_step=None):

    def num(f):
        return int(f * num_features_mult)

    def conv(z, ch, **kwargs):
        if 'parameter_name' not in kwargs:
            kwargs['parameter_name'] = kwargs['name']
        kwargs['name'] = prefix + kwargs['name']
        kwargs['size'] = kwargs.get('size', 3)
        kwargs['parameters'] = kwargs.get('parameters', parameters)
        kwargs['info'] = kwargs.get('info', info)
        kwargs['pre_adjust_batch_norm'] = kwargs.get('pre_adjust_batch_norm', pre_adjust_batch_norm)
        kwargs['activation'] = kwargs.get('activation', activation)
        kwargs['prefix'] = prefix
        kwargs['batch_norm'] = kwargs.get('batch_norm', batch_norm)
        kwargs['phase_test'] = kwargs.get('phase_test', phase_test)
        kwargs['global_step'] = kwargs.get('global_step', global_step)
        if 'previous' in kwargs:
            kwargs['previous'] = prefix + kwargs['previous']
        return vgg_conv(z, num(ch), **kwargs)

    def inner(z, ch, **kwargs):
        if 'parameter_name' not in kwargs:
            kwargs['parameter_name'] = kwargs['name']
        kwargs['name'] = prefix + kwargs['name']
        kwargs['parameters'] = kwargs.get('parameters', parameters)
        kwargs['prefix'] = prefix
        if 'previous' in kwargs:
            kwargs['previous'] = prefix + kwargs['previous']
        return vgg_inner(z, ch, **kwargs)

    #pool = functools.partial(ops.max_pool, info=info)
    def pool(*args, **kwargs):
        kwargs['name'] = prefix + kwargs['name']
        kwargs['info'] = kwargs.get('info', info)
        return ops.max_pool(*args, **kwargs)

    def dropout(z, rate, **kwargs):
        kwargs['phase_test'] = kwargs.get('phase_test', phase_test)
        kwargs['info'] = kwargs.get('info', info)
        kwargs['name'] = prefix + kwargs['name']
        if use_dropout:
            return ops.dropout(z, rate, **kwargs)
        else:
            return z
    #dropout = functools.partial(ops.dropout, phase_test=phase_test, info=info)

    z = x
    if hole == 4:
        apool = functools.partial(ops.atrous_avg_pool, info=info, padding='SAME')
        assert convolutional
        #aconv = functools.partial(_atrous_conv, size=3, parameters=parameters,
                                 #info=info, pre_adjust_batch_norm=pre_adjust_batch_norm)
        z = conv(z, 64, name='conv1_1')
        z = conv(z, 64, name='conv1_2', previous='conv1_1')
        z = pool(z, 2, name='pool1')
        z = conv(z, 128, name='conv2_1', previous='conv1_2')
        z = conv(z, 128, name='conv2_2', previous='conv2_1')
        z = pool(z, 2, name='pool2')
        z = conv(z, 256, name='conv3_1', previous='conv2_2')
        z = conv(z, 256, name='conv3_2', previous='conv3_1')
        z = conv(z, 256, name='conv3_3', previous='conv3_2')
        z = pool(z, 2, name='pool3')
        z = conv(z, 512, name='conv4_1', previous='conv3_3')
        z = conv(z, 512, name='conv4_2', previous='conv4_1')
        z = conv(z, 512, name='conv4_3', previous='conv4_2')
        z = pool(z, 2, stride=1, name='pool4')
        z = conv(z, 512, hole=2, name='conv5_1', previous='conv4_3')
        z = conv(z, 512, hole=2, name='conv5_2', previous='conv5_1')
        z = conv(z, 512, hole=2, name='conv5_3', previous='conv5_2')
        z = apool(z, 2, rate=2, name='pool5')
        z = conv(z, 4096, size=7, hole=4,
                 padding='SAME',
                 name='fc6_pre', parameter_name='fc6', previous='conv5_3')
        z = dropout(z, 0.5, name='fc6')
        z = conv(z, 4096, size=1, name='fc7_pre', parameter_name='fc7', previous='fc6')
        z = dropout(z, 0.5, name='fc7')
    else:
        z = conv(z, 64, name='conv1_1')
        if limit == 1: return z
        z = conv(z, 64, name='conv1_2', previous='conv1_1')
        z = pool(z, 2, name='pool1')
        if limit == 2: return z
        z = conv(z, 128, name='conv2_1', previous='conv1_2')
        if limit == 3: return z
        z = conv(z, 128, name='conv2_2', previous='conv2_1')
        z = pool(z, 2, name='pool2')
        if limit == 4: return z
        z = conv(z, 256, name='conv3_1', previous='conv2_2')
        if limit == 5: return z
        z = conv(z, 256, name='conv3_2', previous='conv3_1')
        if limit == 6: return z
        z = conv(z, 256, name='conv3_3', previous='conv3_2')
        z = pool(z, 2, name='pool3')
        if limit == 7: return z
        z = conv(z, 512, name='conv4_1', previous='conv3_3')
        if limit == 8: return z
        z = conv(z, 512, name='conv4_2', previous='conv4_1')
        if limit == 9: return z
        z = conv(z, 512, name='conv4_3', previous='conv4_2')
        z = pool(z, 2, name='pool4')
        if limit == 10: return z
        z = conv(z, 512, name='conv5_1', previous='conv4_3')
        if limit == 11: return z
        z = conv(z, 512, name='conv5_2', previous='conv5_1')
        if limit == 12: return z
        z = conv(z, 512, name='conv5_3', previous='conv5_2')
        z = pool(z, 2, name='pool5')
        if limit == 13: return z
        z = conv(z, 4096, size=7,
                 padding='VALID' if not convolutional else 'SAME',
                 name='fc6_pre', parameter_name='fc6', previous='conv5_3', squeeze=not convolutional)
        z = dropout(z, 0.5, name='fc6')
        info['activations']
        if limit == 14: return z
        z = conv(z, 4096, size=1, name='fc7_pre', parameter_name='fc7', previous='fc6', squeeze=not convolutional)
        z = dropout(z, 0.5, name='fc7')
        if not convolutional and squeezed:
            # Make intermediate activations non-convolutional
            for l in ['fc6', 'fc7']:
                info['activations'][l] = tf.squeeze(info['activations'][l], [1, 2])
            z = info['activations']['fc7']

    if final_layer:
        if convolutional:
            z = conv(z, 1000, info=info, size=1, parameters=parameters, activation=None, name='fc8')
        else:
            z = inner(z, 1000, info=info, parameters=parameters, activation=None, name='fc8')

    return z
