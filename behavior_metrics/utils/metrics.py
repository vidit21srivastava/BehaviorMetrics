#!/usr/bin/env python

"""This module contains the metrics manager.

This module is in charge of generating metrics for a brain execution.

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version.
This program is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.
You should have received a copy of the GNU General Public License along with
this program. If not, see <http://www.gnu.org/licenses/>.
"""

import pandas as pd
import numpy as np
import shutil
import time

from datetime import datetime
from bagpy import bagreader
from utils.logger import logger

from scipy.optimize import fmin, dual_annealing
from scipy.interpolate import CubicSpline

MIN_COMPLETED_DISTANCE_EXPERIMENT = 10
MIN_PERCENTAGE_COMPLETED_EXPERIMENT = 0
MIN_EXPERIMENT_TIME = 25
LAP_COMPLETED_PERCENTAGE = 100


def is_finish_line(point, start_point):
    try:
        current_point = np.array([point['pose.pose.position.x'], point['pose.pose.position.y']])
    except IndexError:
        current_point = point
    start_point = np.array([start_point['pose.pose.position.x'], start_point['pose.pose.position.y']])

    dist = (start_point - current_point) ** 2
    dist = np.sum(dist, axis=0)
    dist = np.sqrt(dist)
    if dist <= 1.0:
        return True
    return False


def circuit_distance_completed(checkpoints, lap_point):
    previous_point = []
    diameter = 0
    for i, point in enumerate(checkpoints):
        current_point = np.array([point['pose.pose.position.x'], point['pose.pose.position.y']])
        if i != 0:
            dist = (previous_point - current_point) ** 2
            dist = np.sum(dist, axis=0)
            dist = np.sqrt(dist)
            diameter += dist
        if point is lap_point:
            break
        previous_point = current_point
    return diameter


def read_perfect_lap_rosbag(ground_truth_lap_file):
    bag_reader = bagreader(ground_truth_lap_file)
    csv_files = []
    for topic in bag_reader.topics:
        data = bag_reader.message_by_topic(topic)
        csv_files.append(data)

    ground_truth_file_split = ground_truth_lap_file.split('.bag')[0]
    data_file = ground_truth_file_split + '/F1ROS-odom.csv'
    dataframe_pose = pd.read_csv(data_file)
    perfect_lap_checkpoints = []
    for index, row in dataframe_pose.iterrows():
        perfect_lap_checkpoints.append(row)

    start_point = perfect_lap_checkpoints[0]
    lap_point = 0
    for ckp_iter, point in enumerate(perfect_lap_checkpoints):
        if ckp_iter > 100 and is_finish_line(point, start_point):
            if type(lap_point) == int:
                lap_point = point
                break

    circuit_diameter = circuit_distance_completed(perfect_lap_checkpoints, lap_point)
    shutil.rmtree(ground_truth_lap_file.split('.bag')[0])
    return perfect_lap_checkpoints, circuit_diameter


def get_metrics(stats_filename, perfect_lap_checkpoints, circuit_diameter):
    experiment_metrics = {}
    bag_reader = bagreader(stats_filename)
    csv_files = []
    for topic in bag_reader.topics:
        data = bag_reader.message_by_topic(topic)
        csv_files.append(data)

    data_file = stats_filename.split('.bag')[0] + '/F1ROS-odom.csv'
    dataframe_pose = pd.read_csv(data_file)
    checkpoints = []
    for index, row in dataframe_pose.iterrows():
        checkpoints.append(row)

    data_file = stats_filename.split('.bag')[0] + '/clock.csv'
    dataframe_pose = pd.read_csv(data_file)
    clock_points = []
    for index, row in dataframe_pose.iterrows():
        clock_points.append(row)
    start_clock = clock_points[0]
    lap_point = 0
    start_point = checkpoints[0]
    previous_lap_point = 0
    first_previous_lap_point = 0
    laps = 0
    for ckp_iter, point in enumerate(checkpoints):
        if ckp_iter != 0 and point['header.stamp.secs'] - 10 > start_point['header.stamp.secs'] \
                and is_finish_line(point, start_point):
            if type(lap_point) == int:
                lap_point = point
            if abs(ckp_iter - previous_lap_point) > 20:
                laps += 1
                previous_lap_point = ckp_iter
                if first_previous_lap_point == 0:
                    first_previous_lap_point = ckp_iter
    seconds_start = start_clock['clock.secs']
    seconds_end = clock_points[len(clock_points) - 1]['clock.secs']

    experiment_metrics = get_distance_completed(experiment_metrics, checkpoints)
    experiment_metrics = get_average_speed(experiment_metrics, seconds_start, seconds_end)
    experiment_metrics = get_percentage_completed(experiment_metrics, checkpoints, perfect_lap_checkpoints,
                                                  seconds_start, seconds_end, laps)
    experiment_metrics = get_lap_completed_stats(experiment_metrics, circuit_diameter, first_previous_lap_point, lap_point,
                                                 start_clock, clock_points, checkpoints)
    experiment_metrics['experiment_total_time'] = seconds_end - seconds_start
    logger.info('* Experiment total time ---> ' + str(experiment_metrics['experiment_total_time']))
    shutil.rmtree(stats_filename.split('.bag')[0])
    return experiment_metrics


def get_distance_completed(experiment_metrics, checkpoints):
    end_point = checkpoints[len(checkpoints) - 1]
    experiment_metrics['completed_distance'] = circuit_distance_completed(checkpoints, end_point)
    logger.info('* Completed distance ---> ' + str(experiment_metrics['completed_distance']))
    return experiment_metrics


def get_average_speed(experiment_metrics, seconds_start, seconds_end):
    experiment_metrics['average_speed'] = experiment_metrics['completed_distance'] / (seconds_end - seconds_start)
    logger.info('* Average speed ---> ' + str(experiment_metrics['average_speed']))
    return experiment_metrics


def get_percentage_completed(experiment_metrics, checkpoints, perfect_lap_checkpoints, seconds_start, seconds_end, laps):
    # Find last and first checkpoints for retrieving percentage completed
    first_checkpoint = checkpoints[0]
    first_checkpoint = np.array([first_checkpoint['pose.pose.position.x'], first_checkpoint['pose.pose.position.y']])
    last_checkpoint = checkpoints[len(checkpoints) - 1]
    last_checkpoint = np.array([last_checkpoint['pose.pose.position.x'], last_checkpoint['pose.pose.position.y']])
    min_distance_first = 100
    min_distance_last = 100
    first_perfect_checkpoint_position = 0
    last_perfect_checkpoint_position = 0
    for i, point in enumerate(perfect_lap_checkpoints):
        current_point = np.array([point['pose.pose.position.x'], point['pose.pose.position.y']])
        if i != 0:
            dist = (first_checkpoint - current_point) ** 2
            dist = np.sum(dist, axis=0)
            dist = np.sqrt(dist)
            if dist < min_distance_first:
                min_distance_first = dist
                first_perfect_checkpoint_position = i

            dist = (last_checkpoint - current_point) ** 2
            dist = np.sum(dist, axis=0)
            dist = np.sqrt(dist)
            if dist < min_distance_last:
                min_distance_last = dist
                last_perfect_checkpoint_position = i
    if first_perfect_checkpoint_position > last_perfect_checkpoint_position and experiment_metrics['completed_distance'] \
            > MIN_COMPLETED_DISTANCE_EXPERIMENT and seconds_end - seconds_start > MIN_EXPERIMENT_TIME:
        experiment_metrics['percentage_completed'] = (((len(perfect_lap_checkpoints) - first_perfect_checkpoint_position
                                                        + last_perfect_checkpoint_position) / len(perfect_lap_checkpoints))
                                                      * 100) + laps * 100
    else:
        if seconds_end - seconds_start > MIN_EXPERIMENT_TIME:
            # if the percectage is very close to 100%, remove lap.
            if abs((((last_perfect_checkpoint_position - first_perfect_checkpoint_position) / len(perfect_lap_checkpoints)) * 100) - 100) < 1 and laps >= 1:
                laps -= 1
            experiment_metrics['percentage_completed'] = \
                (((last_perfect_checkpoint_position - first_perfect_checkpoint_position) / len(perfect_lap_checkpoints))
                 * 100) + laps * 100
        else:
            experiment_metrics['percentage_completed'] = \
                (((last_perfect_checkpoint_position - first_perfect_checkpoint_position) / len(perfect_lap_checkpoints))
                 * 100)
    logger.info('* Percentage completed ---> ' + str(experiment_metrics['percentage_completed']))
    experiment_metrics = get_robot_position_deviation_score(perfect_lap_checkpoints, checkpoints, experiment_metrics)
    return experiment_metrics


def get_robot_position_deviation_score(perfect_lap_checkpoints, checkpoints, experiment_metrics):
    min_dists = []

    # Get list of points
    point_x = []
    point_y = []
    point_t = []
    for x, checkpoint in enumerate(checkpoints):
        point_x.append(checkpoint['pose.pose.position.x'])
        point_y.append(checkpoint['pose.pose.position.y'])
        point_t.append(x)

    # Generate a natural spline from points
    spline_x = CubicSpline(point_t, point_x, bc_type='natural')
    spline_y = CubicSpline(point_t, point_y, bc_type='natural')

    # Rotate the x and y to start according to checkpoints
    min_dist = 100
    index_t = -1
    perfect_x = []
    perfect_y = []
    for i, checkpoint in enumerate(perfect_lap_checkpoints):
        x = checkpoint['pose.pose.position.x']
        y = checkpoint['pose.pose.position.y']
        perfect_x.append(x)
        perfect_y.append(y)

        dist = np.sqrt((point_x[0] - x) ** 2 + (point_y[0] - y) ** 2)
        if min_dist > dist:
            min_dist = dist
            index_t = i

    perfect_x = perfect_x[index_t:] + perfect_x[:index_t]
    perfect_y = perfect_y[index_t:] + perfect_y[:index_t]

    # Iterate through checkpoints and calculate minimum distance
    previous_t = 0
    perfect_index = 0
    count_same_t = 0
    while True:
        x = perfect_x[perfect_index]
        y = perfect_y[perfect_index]

        point = np.array([x, y])
        distance_function = lambda t: np.sqrt((point[0] - spline_x(t)) ** 2 + (point[1] - spline_y(t)) ** 2)

        # Local Optimization for minimum distance
        current_t = fmin(distance_function, np.array([previous_t]), disp=False)[0]
        min_dist = distance_function(current_t)

        # Global Optimization if minimum distance is greater than expected
        # OR
        # at start checkpoints, since the car may be at start position during initialization (NN Brain)
        if min_dist > 1 or perfect_index in [0, 1, 2]:
            min_bound = previous_t
            max_bound = previous_t + 100
            current_t = dual_annealing(distance_function, bounds=[(min_bound, max_bound)]).x[0]
            min_dist = distance_function(current_t)

        # Two termination conditions:
        # 1. Loop only till all the available points
        if current_t > point_t[-1] - 1:
            break

        # 2. Terminate when converging to same point on spline
        if abs(current_t - previous_t) < 0.01:
            count_same_t += 1
            if count_same_t > 3:
                print("Unexpected Behavior: Converging to same point")
                break
        else:
            count_same_t = 0

        previous_t = current_t
        min_dists.append(1000 ** min_dist)
        perfect_index = (perfect_index + 1) % len(perfect_x)

    experiment_metrics['position_deviation_mae'] = sum(min_dists) / len(min_dists)
    experiment_metrics['position_deviation_total_err'] = sum(min_dists)
    logger.info('* Position deviation MAE ---> ' + str(experiment_metrics['position_deviation_mae']))
    logger.info('* Position deviation total error ---> ' + str(experiment_metrics['position_deviation_total_err']))
    return experiment_metrics


def get_lap_completed_stats(experiment_metrics, circuit_diameter, first_previous_lap_point, lap_point, start_clock, clock_points, checkpoints):
    # If lap is completed, add more statistic information
    if type(lap_point) is not int and experiment_metrics['percentage_completed'] > LAP_COMPLETED_PERCENTAGE:
        seconds_start = start_clock['clock.secs']
        seconds_end = clock_points[int(len(clock_points) * (first_previous_lap_point / len(checkpoints)))]['clock.secs']
        experiment_metrics['lap_seconds'] = seconds_end - seconds_start
        experiment_metrics['circuit_diameter'] = circuit_diameter
        logger.info('* Lap seconds ---> ' + str(experiment_metrics['lap_seconds']))
        logger.info('* Circuit diameter ---> ' + str(experiment_metrics['circuit_diameter']))
    else:
        logger.info('Lap not completed')
    return experiment_metrics