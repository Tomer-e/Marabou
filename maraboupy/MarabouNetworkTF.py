'''
/* *******************                                                        */
/*! \file MarabouNetworkTF.py
 ** \verbatim
 ** Top contributors (to current version):
 **   Christopher Lazarus, Shantanu Thakoor, Chelsea Sidrane
 ** This file is part of the Marabou project.
 ** Copyright (c) 2017-2019 by the authors listed in the file AUTHORS
 ** in the top-level source directory) and their institutional affiliations.
 ** All rights reserved. See the file COPYING in the top-level source
 ** directory for licensing information.\endverbatim
 **
 ** \brief [[ Add one-line brief description here ]]
 **
 ** [[ Add lengthier description here ]]
 **/
'''

import numpy as np
from tensorflow.python.framework import tensor_util
from tensorflow.python.framework import graph_util
import os
os.environ['TF_CPP_MIN_LOG_LEVEL']='2'
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"

import tensorflow as tf
from maraboupy import MarabouUtils
from maraboupy import MarabouNetwork

class MarabouNetworkTF(MarabouNetwork.MarabouNetwork):
    def __init__(self, filename, inputNames=None, outputName=None, savedModel=False, savedModelTags=[]):
        """
        Constructs a MarabouNetworkTF object from a frozen Tensorflow protobuf or SavedModel

        Args:
            filename: (string) If savedModel is false, path to the frozen graph .pb file.
                               If savedModel is true, path to SavedModel folder, which
                               contains .pb file and variables subdirectory.
            inputName: (string) optional, name of operation corresponding to input.
            outputName: (string) optional, name of operation corresponding to output.
            savedModel: (bool) If false, load frozen graph. If true, load SavedModel object.
            savedModelTags: (list of strings) If loading a SavedModel, the user must specify tags used.
        """
        super().__init__()
        self.biasAddRelations = list()
        self.readFromPb(filename, inputNames, outputName, savedModel, savedModelTags)
        self.processBiasAddRelations()

    def clear(self):
        """
        Reset values to represent empty network
        """
        super().clear()
        self.madeGraphEquations = []
        self.varMap = dict()
        self.shapeMap = dict()
        self.inputOps = None
        self.outputOp = None
        self.sess = None
        self.biasAddRelations = list()

    def readFromPb(self, filename, inputNames, outputName, savedModel, savedModelTags):
        """
        Constructs a MarabouNetworkTF object from a frozen Tensorflow protobuf or SavedModel

        Args:
            filename: (string) If savedModel is false, path to the frozen graph .pb file.
                               If savedModel is true, path to SavedModel folder, which
                               contains .pb file and variables subdirectory.
            inputName: (string) optional, name of operation corresponding to input.
            outputName: (string) optional, name of operation corresponding to output.
            savedModel: (bool) If false, load frozen graph. If true, load SavedModel object.
            savedModelTags: (list of strings) If loading a SavedModel, the user must specify tags used.
        """

        if savedModel:
            ### Read SavedModel ###
            sess = tf.Session()
            tf.saved_model.loader.load(sess, savedModelTags, filename)

            ### Simplify graph using outputName, which must be specified for SavedModel ###
            simp_graph_def = graph_util.convert_variables_to_constants(sess,sess.graph.as_graph_def(),[outputName])
            with tf.Graph().as_default() as graph:
                tf.import_graph_def(simp_graph_def, name="")
            self.sess = tf.Session(graph=graph)
            ### End reading SavedModel

        else:
            ### Read protobuf file and begin session ###
            with tf.gfile.GFile(filename, "rb") as f:
                graph_def = tf.GraphDef()
                graph_def.ParseFromString(f.read())
            with tf.Graph().as_default() as graph:
                tf.import_graph_def(graph_def, name="")
            self.sess = tf.Session(graph=graph)
            ### END reading protobuf ###

        ### Find operations corresponding to input and output ###
        if inputNames: # is not None
            inputOps = []
            for i in inputNames:
               inputOps.append(self.sess.graph.get_operation_by_name(i))
        else: # If there is just one placeholder, use it as input
            ops = self.sess.graph.get_operations()
            placeholders = [x for x in ops if x.node_def.op == 'Placeholder']
            inputOps = placeholders
        if outputName:
            outputOp = self.sess.graph.get_operation_by_name(outputName)
        else: # Assume that the last operation is the output
            outputOp = self.sess.graph.get_operations()[-1]
        self.setInputOps(inputOps)
        self.setOutputOp(outputOp)
        ### END finding input/output operations ###

        ### Generate equations corresponding to network ###
        self.foundnInputFlags = 0
        self.makeGraphEquations(self.outputOp)
        assert self.foundnInputFlags == len(inputOps)
        ### END generating equations ###

    def setInputOps(self, ops):
        """
        Function to set input operations
        Arguments:
            [ops]: (tf.op) list representing input
        """
        self.inputVars = []
        for op in ops:
            try:
                shape = tuple(op.outputs[0].shape.as_list())
                self.shapeMap[op] = shape
            except:
                self.shapeMap[op] = [None]

                ## TODO: this is only for pensieve, the number of inputs to the network is unknown for some reason
                if op.name =="actor/InputData/X" or op.name == "critic/InputData/X":
                    self.shapeMap[op] = (None, 6, 8)
            self.inputVars.append(self.opToVarArray(op))
        self.inputOps = ops


    def setOutputOp(self, op):
        """
        Function to set output operation
        Arguments:
            op: (tf.op) Representing output
        """
        try:
            shape = tuple(op.outputs[0].shape.as_list())
            self.shapeMap[op] = shape
        except:
            self.shapeMap[op] = [None]
        self.outputOp = op
        self.outputVars = self.opToVarArray(self.outputOp)

    def opToVarArray(self, x):
        """
        Function to find variables corresponding to operation
        Arguments:
            x: (tf.op) the operation to find variables for
        Returns:
            v: (np array) of variable numbers, in same shape as x
        """
        if x in self.varMap:
            return self.varMap[x]

        ### Find number of new variables needed ###
        if x in self.shapeMap:
            shape = self.shapeMap[x]
            shape = [a if a is not None else 1 for a in shape]
        else:
            shape = [a if a is not None else 1 for a in x.outputs[0].get_shape().as_list()]
        size = 1
        for a in shape:
            size*=a
        ### END finding number of new variables ###

        v = np.array([self.getNewVariable() for _ in range(size)]).reshape(shape)
        self.varMap[x] = v
        assert all([np.equal(np.mod(i, 1), 0) for i in v.reshape(-1)]) # check if integers
        return v




    def getValues(self, op):
        """
        Function to find underlying constants/variables representing operation
        Arguments:
            op: (tf.op) to get values of
        Returns:
            values: (np array) of scalars or variable numbers depending on op
        """
        input_ops = [i.op for i in op.inputs]
        ### Operations not requiring new variables ###
        if op.node_def.op == 'Identity':
            return self.getValues(input_ops[0])
        if op.node_def.op == 'Squeeze':
            print ("Squeeze inputs_ops = ",input_ops)
            prevValues = self.getValues(input_ops[0])
            print("Squeeze prevValues = ", prevValues)
            print("Squeeze prevValues.shape = ", prevValues.shape)
            squeeze_dims = op.node_def.attr["squeeze_dims"].list.i
            print("Squeeze squeeze_dims = ", squeeze_dims)
            axis = op.node_def.attr["axis"].list.i
            print("Squeeze axis = ", axis)
            assert (len(axis) == 0 or len (squeeze_dims) == 0)
            prevValues_shape = prevValues.shape
            squeeze = axis if len(axis)> 0 else squeeze_dims
            new_shape = []
            i = 0
            for val in prevValues_shape:
                print("i",i , "val", val)
                if i in squeeze:
                    print("removing",i,"val",val)
                    i+=1
                    continue
                new_shape.append(val)
                i+=1
            print(new_shape)
            # TODO: check about "negative number for axis" (counted backward from the end)
            return prevValues.reshape(new_shape)
        if op.node_def.op == 'ExpandDims':
            # print ("ExpandDims inputs_ops = ",input_ops[1])
            dim = self.getValues(input_ops[1])
            prevValues = self.getValues(input_ops[0])
            print ("ExpandDims inputs[1] (dim) = ",dim)
            print ("ExpandDims inputs[0] (values) = ",prevValues)
            print ("ExpandDims values shape = ",prevValues.shape)
            prevValues_shape = prevValues.shape
            print ("ExpandDims op.inputs[0].shape.dims = ",op.inputs[0].shape.dims)
            new_shape = []
            i = 0
            for val in prevValues_shape:
                if i==dim:
                    new_shape.append(1)
                new_shape.append(val)
                i+=1
            print(new_shape)
            # TODO:need also to support - "if you specify a negative number for axis it is counted backward from the end"
            return prevValues.reshape(new_shape)
        if op.node_def.op in ['Reshape']:
            if input_ops[1].node_def.op == 'Pack':
                prevValues = self.getValues(input_ops[0])
                input_dims = op.inputs[0].shape.dims
                input_size = np.prod(np.array([d.value for d in input_dims])[1:])
                shape = (-1, input_size)
            else:
                prevValues = [self.getValues(i) for i in input_ops]
                shape = prevValues[1]
            return np.reshape(prevValues[0], shape)
        if op.node_def.op == 'ConcatV2':
            prevValues = [self.getValues(i) for i in input_ops]
            N = op.node_def.attr["N"].i
            values = prevValues[0:N]
            print("ConcatV2 values = ", prevValues)
            print( "concat attr.N =  " , N)

            axis = prevValues[N]
            # print ("axis = ",axis)
            return np.concatenate(values, axis=axis)
        if op.node_def.op == 'Split':
            # print("--------------------------------------Split--------------------------------------")
            cur_op = op.node_def.op
            prevValues = [self.getValues(i) for i in input_ops]
            # print(np.split(prevValues[1], indices_or_sections=2, axis = 1))
            return np.split(prevValues[1], indices_or_sections=2, axis = 1)
        if op.node_def.op == 'Const':
            tproto = op.node_def.attr['value'].tensor
            return tensor_util.MakeNdarray(tproto)

        if op.node_def.op in ['StridedSlice']:

            prevValues = [self.getValues(i) for i in input_ops]

            assert (len(prevValues) == 4) ## or len(prevValues) == 3)

            input_ = prevValues[0]
            print(tf.shape(op.inputs[0]))
            print("inputs (actual name = "+input_ops[0].name+")")
            print(input_)
            input_shape = input_.shape
            print(input_shape)

            begin= prevValues[1]
            print("begin (actual name = "+input_ops[1].name+")")
            print(begin)
            assert (len(begin) == 3) # Todo: support any shape
            end = prevValues[2]
            print("end (actual name = " + input_ops[2].name + ")")
            print(end)
            assert (len(end) == 3)

            strides = prevValues[3]
            print("strides (actual name = " + input_ops[3].name + ")")
            print(strides)
            assert (len(strides) == 3)

            for stride in strides:
                assert (stride == 1)  # only stride = 1 is supported

            # values = input_[begin[0]:end[0],begin[1]:end[1],begin[2]:end[2]]
            # print(values)
            def to_reversed_bit_array(num):
                return (format(num, '03b')[::-1])


            begin_mask = op.node_def.attr["begin_mask"].i
            print ("begin_mask =",begin_mask)
            begin_mask_ba = to_reversed_bit_array(begin_mask)
            print("begin_mask =", begin_mask)


            ellipsis_mask = op.node_def.attr["ellipsis_mask"].i
            print ("ellipsis_mask =",ellipsis_mask)
            ellipsis_mask_ba = to_reversed_bit_array(ellipsis_mask)
            print("ellipsis_mask_ba =", ellipsis_mask_ba)

            end_mask = op.node_def.attr["end_mask"].i
            print ("end_mask =",end_mask)
            end_mask_ba = to_reversed_bit_array(end_mask)
            print("end_mask_ba =", end_mask_ba)

            new_axis_mask = op.node_def.attr["new_axis_mask"].i
            print ("new_axis_mask =",new_axis_mask)
            new_axis_mask_ba = to_reversed_bit_array(new_axis_mask)
            print("new_axis_mask_ba =", new_axis_mask_ba)

            shrink_axis_mask = op.node_def.attr["shrink_axis_mask"].i
            print ("shrink_axis_mask =",shrink_axis_mask)
            shrink_axis_mask_ba = to_reversed_bit_array(shrink_axis_mask)
            print("shrink_axis_mask_ba =", shrink_axis_mask_ba)

            print()

            actual_begin = begin.copy()
            actual_end = end.copy()
            dims = len(input_shape)
            for i in range(len(begin)):
                # if begin[i]<0:
                #     actual_end[i] =  len(begin) + begin[i]
                # if end[i]<0:
                #     actual_begin[i] =  len(end) + end[i]

                if begin_mask_ba[i] == '1':
                    actual_begin[i] = 0
                if end_mask_ba[i] == '1':
                    actual_end[i] = input_shape[i]
                if shrink_axis_mask_ba[i] == '1':
                    dims-=1
                    if begin[i]>=0:
                        actual_begin = begin[i]
                        actual_end = actual_begin[i] + 1
                    else:
                        actual_begin[i] = input_shape[i] + begin[i]
                        actual_end[i] = actual_begin[i] + 1

            print("actual_begin",actual_begin)
            print("actual_end",actual_end)
            values = input_[actual_begin[0]:actual_end[0],actual_begin[1]:actual_end[1],actual_begin[2]:actual_end[2]]
            print(values)
            if dims == 3: return values
            if dims == 2: return values[0]
            if dims == 1: return values[0][0]
            if dims == 0: return values[0][0][0]
        # return self.getValues(input_ops[0])
        ### END operations not requiring new variables ###
        if op.node_def.op in ['MatMul', 'BiasAdd', 'Add', 'Sub', 'Relu', 'MaxPool', 'Conv2D', 'Placeholder','Mul']:
            # need to create variables for these
            return self.opToVarArray(op)

        raise NotImplementedError

    def isVariable(self, op):
        """
        Function returning whether operation represents variable or constant
        Arguments:
            op: (tf.op) representing operation in network
        Returns:
            isVariable: (bool) true if variable, false if constant
        """
        if op.node_def.op == 'Placeholder':
            return True
        if op.node_def.op == 'Const':
            return False
        return any([self.isVariable(i.op) for i in op.inputs])

    def matMulEquations(self, op):
        """
        Function to generate equations corresponding to matrix multiplication
        Arguments:
            op: (tf.op) representing matrix multiplication operation
        """

        ### Get variables and constants of inputs ###
        input_ops = [i.op for i in op.inputs]
        prevValues = [self.getValues(i) for i in input_ops]
        curValues = self.getValues(op)
        aTranspose = op.node_def.attr['transpose_a'].b
        bTranspose = op.node_def.attr['transpose_b'].b
        A = prevValues[0]
        B = prevValues[1]
        if aTranspose:
            A = np.transpose(A)
        if bTranspose:
            B = np.transpose(B)
        assert (A.shape[0], B.shape[1]) == curValues.shape
        assert A.shape[1] == B.shape[0]
        m, n = curValues.shape
        p = A.shape[1]
        ### END getting inputs ###

        ### Generate actual equations ###
        for i in range(m):
            for j in range(n):
                e = []
                e = MarabouUtils.Equation()
                for k in range(p):
                    e.addAddend(B[k][j], A[i][k])
                e.addAddend(-1, curValues[i][j])
                e.setScalar(0.0)
                self.addEquation(e)

    def biasAddEquations(self, op):
        """
        Function to generate equations corresponding to bias addition
        Arguments:
            op: (tf.op) representing bias add operation
        """
        ### Get variables and constants of inputs ###
        input_ops = [i.op for i in op.inputs]
        assert len(input_ops) == 2
        prevValues = [self.getValues(i) for i in input_ops]
        curValues = self.getValues(op)
        prevVars = prevValues[0].reshape(-1)
        prevConsts = prevValues[1].reshape(-1)
        # broadcasting
        prevConsts = np.tile(prevConsts, len(prevVars)//len(prevConsts))
        curVars = curValues.reshape(-1)
        assert len(prevVars)==len(curVars) and len(curVars)==len(prevConsts)
        ### END getting inputs ###

        ### Do not generate equations, as these can be eliminated ###
        for i in range(len(prevVars)):
            # prevVars = curVars - prevConst
            self.biasAddRelations += [(prevVars[i], curVars[i], -prevConsts[i])]

    def processBiasAddRelations(self):
        """
        Either add an equation representing a bias add,
        Or eliminate one of the two variables in every other relation
        """
        biasAddUpdates = dict()
        participations = [rel[0] for rel in self.biasAddRelations] + \
                            [rel[1] for rel in self.biasAddRelations]
        for (x, xprime, c) in self.biasAddRelations:
            # x = xprime + c
            # replace x only if it does not occur anywhere else in the system
            if self.lowerBoundExists(x) or self.upperBoundExists(x) or \
                    self.participatesInPLConstraint(x) or \
                    len([p for p in participations if p == x]) > 1:
                e = MarabouUtils.Equation()
                e.addAddend(1.0, x)
                e.addAddend(-1.0, xprime)
                e.setScalar(c)
                self.addEquation(e)
            else:
                biasAddUpdates[x] = (xprime, c)
                self.setLowerBound(x, 0.0)
                self.setUpperBound(x, 0.0)

        for equ in self.equList:
            participating = equ.getParticipatingVariables()
            for x in participating:
                if x in biasAddUpdates: # if a variable to remove is part of this equation
                    xprime, c = biasAddUpdates[x]
                    equ.replaceVariable(x, xprime, c)

    def addEquations(self, op):
        """
        Function to generate equations corresponding to bias addition
        Arguments:
            op: (tf.op) representing bias add operation
        """
        input_ops = [i.op for i in op.inputs]
        assert len(input_ops) == 2
        input1 = input_ops[0]
        input2 = input_ops[1]
        assert self.isVariable(input1)
        if self.isVariable(input2):
            curVars = self.getValues(op).reshape(-1)
            prevVars1 = self.getValues(input1).reshape(-1)
            prevVars2 = self.getValues(input2).reshape(-1)
            assert len(prevVars1) == len(prevVars2)
            assert len(curVars) == len(prevVars1)
            for i in range(len(curVars)):
                e = MarabouUtils.Equation()
                e.addAddend(1, prevVars1[i])
                e.addAddend(1, prevVars2[i])
                e.addAddend(-1, curVars[i])
                e.setScalar(0.0)
                self.addEquation(e)
        else:
            self.biasAddEquations(op)


    def mulEquations(self, op):
        """
        Function to generate equations corresponding to mul
        Arguments:
            op: (tf.op) representing  mul operation
        """

        input_ops = [i.op for i in op.inputs]
        # print (input_ops)
        assert len(input_ops) == 2
        input1 = input_ops[0]
        input2 = input_ops[1]
        assert (self.isVariable(input1) and not self.isVariable(input2)) or (not self.isVariable(input1) and self.isVariable(input2))
        curVars = self.getValues(op).reshape(-1)
        # print(curVars)
        prevVars1 = self.getValues(input1).reshape(-1)
        # print(prevVars1)
        prevVars2 = self.getValues(input2).reshape(-1)
        # print(prevVars2)

        assert len(prevVars1) == len(prevVars2)
        assert len(curVars) == len(prevVars1)
        # print ("mul curVars len = ",len(curVars))
        if self.isVariable(input1):
            for i in range(len(curVars)):
                e = MarabouUtils.Equation()
                e.addAddend(prevVars2[i], prevVars1[i])
                e.addAddend(-1, curVars[i])
                e.setScalar(0.0)
                self.addEquation(e)
        else: #self.isVariable(input2)
            for i in range(len(curVars)):
                e = MarabouUtils.Equation()
                e.addAddend(prevVars1[i], prevVars2[i])
                e.addAddend(-1, curVars[i])
                e.setScalar(0.0)
                self.addEquation(e)

    def mulEquations2(self, op):
        """
        Function to generate equations corresponding to mul
        Arguments:
            op: (tf.op) representing  mul operation
        """

        ### Get variables and constants of inputs ###
        input_ops = [i.op for i in op.inputs]
        assert len(input_ops) == 2
        # print ("input_ops",input_ops)
        prevValues = [self.getValues(i) for i in input_ops]
        # print("prevValues" ,prevValues)
        curValues = self.getValues(op)
        # print("curValues" ,curValues)

        prevVars = prevValues[0].reshape(-1)
        prevConsts = prevValues[1].reshape(-1)
        # print("prevConsts[0] =",prevConsts[0])
        # print("prevConsts =",prevConsts)
        # broadcasting
        # prevConsts = np.tile(prevConsts, len(prevVars) // len(prevConsts))
        curVars = curValues.reshape(-1)
        assert len(prevVars) == len(curVars) and len(curVars) == len(prevConsts)
        ### END getting inputs ###
        # print ("mul curVars len = ",len(curVars))
        for i in range(len(prevVars)):
            e = MarabouUtils.Equation()
            e.addAddend(prevConsts[i], prevVars[i])
            e.addAddend(-1, curVars[i])
            e.setScalar(0.0)
            self.addEquation(e)


    def subEquations(self, op): 
        """
        Function to generate equations corresponding to subtraction
        Arguments:
            op: (tf.op) representing sub operation
        """
        input_ops = [i.op for i in op.inputs]
        assert len(input_ops) == 2
        input1 = input_ops[0]
        input2 = input_ops[1]
        assert self.isVariable(input1)
        if self.isVariable(input2):
            curVars = self.getValues(op).reshape(-1)
            prevVars1 = self.getValues(input1).reshape(-1)
            prevVars2 = self.getValues(input2).reshape(-1)
            assert len(prevVars1) == len(prevVars2)
            assert len(curVars) == len(prevVars1)
            for i in range(len(curVars)):
                e = MarabouUtils.Equation()
                e.addAddend(1, prevVars1[i])
                e.addAddend(-1, prevVars2[i])
                e.addAddend(-1, curVars[i])
                e.setScalar(0.0)
                self.addEquation(e)
        else:
            self.biasAddEquations(op)


    def conv2DEquations(self, op):
        """
        Function to generate equations corresponding to 2D convolution operation
        Arguments:
            op: (tf.op) representing conv2D operation
        """

        ### Get variables and constants of inputs ###
        input_ops = [i.op for i in op.inputs]
        prevValues = [self.getValues(i) for i in input_ops]
        curValues = self.getValues(op)
        padding = op.node_def.attr['padding'].s.decode()
        strides = list(op.node_def.attr['strides'].list.i)
        prevValues, prevConsts = prevValues[0], prevValues[1]
        _, out_height, out_width, out_channels = curValues.shape
        _, in_height,  in_width,  in_channels  = prevValues.shape
        filter_height, filter_width, filter_channels, num_filters = prevConsts.shape
        assert filter_channels == in_channels
        assert out_channels == num_filters
        # Use padding to determine top and left offsets
        # See https://github.com/tensorflow/tensorflow/blob/master/tensorflow/core/kernels/quantized_conv_ops.cc#L51
        if padding=='SAME':
            pad_top  = ((out_height - 1) * strides[1] + filter_height - in_height) // 2
            pad_left = ((out_width - 1) * strides[2] + filter_width - in_width) // 2
        elif padding=='VALID':
            pad_top  = ((out_height - 1) * strides[1] + filter_height - in_height + 1) // 2
            pad_left = ((out_width - 1) * strides[2] + filter_width - in_width + 1) // 2
        else:
            raise NotImplementedError
        ### END getting inputs ###

        ### Generate actual equations ###
        # There is one equation for every output variable
        for i in range(out_height):
            for j in range(out_width):
                for k in range(out_channels): # Out_channel corresponds to filter number
                    e = MarabouUtils.Equation()
                    # The equation convolves the filter with the specified input region
                    # Iterate over the filter
                    for di in range(filter_height):
                        for dj in range(filter_width):
                            for dk in range(filter_channels):

                                h_ind = int(strides[1]*i+di - pad_top)
                                w_ind = int(strides[2]*j+dj - pad_left)
                                if h_ind < in_height and h_ind>=0 and w_ind < in_width and w_ind >=0:
                                    var = prevValues[0][h_ind][w_ind][dk]
                                    c = prevConsts[di][dj][dk][k]
                                    e.addAddend(c, var)

                    # Add output variable
                    e.addAddend(-1, curValues[0][i][j][k])
                    e.setScalar(0.0)
                    self.addEquation(e)

    def reluEquations(self, op):
        """
        Function to generate equations corresponding to pointwise Relu
        Arguments:
            op: (tf.op) representing Relu operation
        """

        ### Get variables and constants of inputs ###
        input_ops = [i.op for i in op.inputs]
        prevValues = [self.getValues(i) for i in input_ops]
        curValues = self.getValues(op)
        prev = prevValues[0].reshape(-1)
        cur = curValues.reshape(-1)
        assert len(prev) == len(cur)
        ### END getting inputs ###

        ### Generate actual equations ###
        for i in range(len(prev)):
            self.addRelu(prev[i], cur[i])
        for f in cur:
            self.setLowerBound(f, 0.0)

    def maxpoolEquations(self, op):
        """
        Function to generate maxpooling equations
        Arguments:
            op: (tf.op) representing maxpool operation
        """
        ### Get variables and constants of inputs ###
        input_ops = [i.op for i in op.inputs]
        prevValues = [self.getValues(i) for i in input_ops]
        curValues = self.getValues(op)
        validPadding = op.node_def.attr['padding'].s == b'VALID'
        if not validPadding:
            raise NotImplementedError
        prevValues = prevValues[0]
        strides = list(op.node_def.attr['strides'].list.i)
        ksize = list(op.node_def.attr['ksize'].list.i)
        for i in range(curValues.shape[1]):
            for j in range(curValues.shape[2]):
                for k in range(curValues.shape[3]):
                    maxVars = set()
                    for di in range(strides[1]*i, strides[1]*i + ksize[1]):
                        for dj in range(strides[2]*j, strides[2]*j + ksize[2]):
                            if di < prevValues.shape[1] and dj < prevValues.shape[2]:
                                maxVars.add(prevValues[0][di][dj][k])
                    self.addMaxConstraint(maxVars, curValues[0][i][j][k])

    def makeNeuronEquations(self, op):
        """
        Function to generate equations corresponding to given operation
        Arguments:
            op: (tf.op) for which to generate equations
        """

        if op.node_def.op in ['Identity', 'Reshape', 'Pack', 'Placeholder', 'Const', 'ConcatV2', 'Shape', 'StridedSlice','ExpandDims','Squeeze','Split']:
            return
        if op.node_def.op == 'MatMul':
            self.matMulEquations(op)
        elif op.node_def.op == 'BiasAdd':
            self.biasAddEquations(op)
        elif op.node_def.op == 'Add':
            self.addEquations(op)
        elif op.node_def.op == 'Sub':
            self.subEquations(op)
        elif op.node_def.op == 'Conv2D':
            self.conv2DEquations(op)
        elif op.node_def.op == 'Relu':
            self.reluEquations(op)
        elif op.node_def.op == 'MaxPool':
            self.maxpoolEquations(op)
        elif op.node_def.op == 'Mul':
            self.mulEquations(op)
        else:
            print("Operation ", str(op.node_def.op), " not implemented")
            raise NotImplementedError
    def makeGraphEquations(self, op):
        """
        Function to generate equations for network necessary to calculate op
        Arguments:
            op: (tf.op) representing operation until which we want to generate network equations
        """
        if op in self.madeGraphEquations:
            return
        self.madeGraphEquations += [op]
        if op in self.inputOps:
            self.foundnInputFlags += 1
        in_ops = [x.op for x in op.inputs]
        for x in in_ops:
            self.makeGraphEquations(x)
        self.makeNeuronEquations(op)

    def evaluateWithoutMarabou(self, inputValues):
        """
        Function to evaluate network at a given point using Tensorflow
        Arguments:
            inputValues: list of (np array)s representing inputs to network
        Returns:
            outputValues: (np array) representing output of network
        """
        inputValuesReshaped = []
        for j in range(len(self.inputOps)):
            inputOp = self.inputOps[j]
            inputShape = self.shapeMap[inputOp]
            inputShape = [i if i is not None else 1 for i in inputShape]
            # Try to reshape given input to correct shape
            inputValuesReshaped.append(inputValues[j].reshape(inputShape))

        inputNames = [o.name+":0" for o in self.inputOps]
        feed_dict = dict(zip(inputNames, inputValuesReshaped))
        outputName = self.outputOp.name
        out = self.sess.run(outputName + ":0", feed_dict=feed_dict)

        return out[0]
