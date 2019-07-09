"""Transport model wrapper
"""
# -*- coding: utf-8 -*-

import configparser
import csv
import os
from subprocess import check_output, CalledProcessError
from string import Template

import pandas as pd
import numpy as np
from smif.data_layer.data_array import DataArray
from smif.exception import SmifTimestepResolutionError
from smif.model.sector_model import SectorModel


class BaseTransportWrapper(SectorModel):
    """Base wrapper for the transport model - override class variables in implementations
    """
    _config_filename = 'run_config.ini'
    _template_filename = 'config.properties.template'

    def __init__(self, *args, **kwargs):
        # shared setup
        self._current_timestep = None
        self._set_options()
        super().__init__(*args, **kwargs)

    def _set_options(self):
        this_dir = os.path.dirname(__file__)

        config = configparser.ConfigParser()
        config.read(os.path.join(this_dir, self._config_filename))

        self._templates_dir = os.path.join(this_dir, 'templates')

        if 'run' not in config:
            raise KeyError("Expected '[run]' section in transport run_config.ini")

        if 'jar' in config['run']:
            self._jar_path = os.path.join(this_dir, config['run']['jar'])
        else:
            raise KeyError("Expected 'jar' in transport run_config.ini")

        if 'working_dir' in config['run']:
            self._working_dir = os.path.join(this_dir, config['run']['working_dir'])
            self._input_dir = os.path.join(self._working_dir, 'input')
            self._output_dir = os.path.join(self._working_dir, 'output')
            self._config_path = os.path.join(self._working_dir, 'config.properties')
        else:
            raise KeyError("Expected 'data_dir' in transport run_config.ini")

        if 'optional_args' in config['run']:
            self._optional_args = config['run']['optional_args'].split(" ")
        else:
            self._optional_args = []

    def _output_file_path(self, filename):
        return os.path.join(self._output_dir, str(self._current_timestep), filename)

    def simulate(self, data):
        """Run the transport model

        Arguments
        ---------
        data: smif.data_layer.DataHandle
        """
        try:
            os.mkdir(self._input_dir)
        except FileExistsError:
            pass

        self._current_timestep = data.current_timestep
        self._set_parameters(data)
        self._set_inputs(data)
        self._set_properties(data)
        self._run_model_subprocess(data)
        self._set_outputs(data)

    def _run_model_subprocess(self, data_handle):
        """Run the transport model jar and feed log messages
        into the smif loggerlogger
        """

        working_dir = self._working_dir
        path_to_jar = self._jar_path

        self.logger.info("FROM run.py: Running transport model")
        base_arguments = ['java'] + self._optional_args + [
            '-XX:MaxHeapSize=10g',
            '-cp',
            path_to_jar,
            'nismod.transport.App',
            '-c',
            self._config_path
        ]
        if data_handle.current_timestep == data_handle.base_timestep:
            pass
        else:
            tspt_model_arguments = base_arguments + [
                '-rail',
                str(data_handle.current_timestep),
                str(data_handle.previous_timestep)
            ]
            try:
                self.logger.debug(tspt_model_arguments)
                output = check_output(tspt_model_arguments)
                self.logger.info(output.decode("utf-8"))
            except CalledProcessError as ex:
                self.logger.error(ex.output.decode("utf-8"))
                self.logger.exception("Transport model failed %s", ex)
                raise ex

    def _input_dimension_names(self, input_name, dimension_name):
        return self.inputs[input_name].dim_coords(dimension_name).ids

    def _set_parameters(self, data_handle):
        """Read model parameters from data handle and set up config files
        """
        input_dir = self._input_dir

        # Elasticities for passenger and freight demand
        variables = ['POPULATION', 'GVA', 'TIME', 'COST']
        types = {
            'ETA': os.path.join(input_dir, 'elasticities.csv'),
            'FREIGHT_ETA': os.path.join(
                input_dir, 'elasticitiesFreight.csv')
        }
        for suffix, filename in types.items():
            with open(filename, 'w') as file_handle:
                writer = csv.writer(file_handle)
                writer.writerow(('variable', 'elasticity'))
                for variable in variables:
                    key = "{}_{}".format(variable, suffix)
                    value = float(data_handle.get_parameter(key).as_ndarray())
                    writer.writerow((variable, value))

    def _set_inputs(self, data_handle):
        """Get model inputs from data handle and write to input files
        """
        self._set_population(data_handle)
        self._set_gva(data_handle)

    def _set_population(self, data_handle):
        current_population = data_handle.get_data("population").as_df().reset_index()
        print(current_population)
        current_population['year'] = data_handle.current_timestep

        if data_handle.current_timestep != data_handle.base_timestep:
            previous_population = data_handle.get_data("population").as_df().reset_index()
            previous_population['year'] = data_handle.previous_timestep

            population = pd.concat(
                [previous_population, current_population]
            )
        else:
            population = current_population

        population.population = population.population.astype(int)
        # use region dimension name (could change) for columns
        colname = self.inputs['population'].dims[0]
        population = population.pivot(
            index='year', columns=colname, values='population'
        )
        population_filepath = os.path.join(
            self._input_dir, 'population.csv')
        population.to_csv(population_filepath)

    def _set_gva(self, data_handle):
        current_gva = data_handle.get_data("gva").as_df().reset_index()
        current_gva['year'] = data_handle.current_timestep

        if data_handle.current_timestep != data_handle.base_timestep:
            previous_gva = data_handle.get_previous_timestep_data("gva").as_df().reset_index()
            previous_gva['year'] = data_handle.previous_timestep

            gva = pd.concat(
                [previous_gva, current_gva]
            )
        else:
            gva = current_gva

        # use region dimension name (could change) for columns
        colname = self.inputs['gva'].dims[0]
        gva = gva.pivot(
            index='year', columns=colname, values='gva'
        )
        gva_filepath = os.path.join(self._input_dir, 'gva.csv')
        gva.to_csv(gva_filepath)

    def _set_properties(self, data_handle):
        """Set the transport model properties, such as paths and interventions
        """
        working_dir = self._working_dir
        working_dir_path = str(os.path.abspath(working_dir)).replace('\\', '/')
        path_to_config_template = os.path.join(self._templates_dir, self._template_filename)

        # read config as a Template for easy substitution of values
        with open(path_to_config_template) as template_fh:
            config = Template(template_fh.read())

        intervention_files = []
        for i, intervention in enumerate(data_handle.get_current_interventions().values()):
            fname = self._write_intervention(intervention)
            intervention_files.append("railInterventionFile{} = {}".format(i, fname))

        config_str = config.substitute({
            'relative_path': working_dir_path,
            'intervention_files': '\n'.join(intervention_files),
            'link_travel_time_averaging_weight': \
                float(data_handle.get_parameter('link_travel_time_averaging_weight').data),
            'assignment_iterations': \
                int(data_handle.get_parameter('assignment_iterations').data),
            'prediction_iterations': \
                int(data_handle.get_parameter('prediction_iterations').data),
            'use_route_choice_model': \
                bool(data_handle.get_parameter('use_route_choice_model').data),
        })

        with open(self._config_path, 'w') as template_fh:
            template_fh.write(config_str)

    def _write_intervention(self, intervention):
        """Write a single intervention file, returning the full path
        """
        path = os.path.normpath(os.path.abspath(os.path.join(
            self._input_dir, "{}.properties".format(intervention['name']))))

        # compute start/end year from smif intervention keys
        intervention['startYear'] = intervention['build_year']
        intervention['endYear'] =  intervention['build_year'] + \
            intervention['technical_lifetime']['value']
        del intervention['build_year']
        del intervention['technical_lifetime']

        # fix up path to congestion charging pricing details file
        if 'congestionChargingPricing' in intervention:
            cccp_filename = intervention['congestionChargingPricing']
            intervention['congestionChargingPricing'] = os.path.join(
                self._working_dir, 'data', 'csvfiles', cccp_filename
            )

        with open(path, 'w') as file_handle:
            for key, value in intervention.items():
                file_handle.write("{} = {}\n".format(key, value))

        return path

    def _set_outputs(self, data_handle):
        pass

    def _melt_output(self, name, filename, dims, csv_id_vars, csv_melt_var):
        return pd.read_csv(
            filename
        ).drop(
            'year', axis=1  # ignore the year output, assume it's always current timestep
        ).melt(
            id_vars=csv_id_vars,
            var_name=csv_melt_var,
            value_name=name
        ).rename(
            dims, axis=1
        )

    def _df_to_ndarray(self, output_name, dataframe):
        spec = self.outputs[output_name]
        dataframe.set_index(spec.dims, inplace=True)
        dataarray = DataArray.from_df(spec, dataframe)
        return dataarray.data


class TransportWrapper(BaseTransportWrapper):
    """Wrap the transport model, in 'full' configuration
    """
    _config_filename = 'run_config_full.ini'
    _template_filename = 'gb-config.properties.template'


class SouthamptonTransportWrapper(BaseTransportWrapper):
    """Wrap the transport model, in 'southampton' configuration
    """
    _config_filename = 'run_config_southampton.ini'
    _template_filename = 'southampton-config.properties.template'
