from typing import Dict, Text

import numpy as np

from highway_env import utils
from highway_env.envs.common.abstract import AbstractEnv
from highway_env.envs.common.action import Action
from highway_env.road.road import Road, RoadNetwork
from highway_env.utils import near_split
from highway_env.vehicle.controller import ControlledVehicle
from highway_env.vehicle.kinematics import Vehicle

from typing import List, Tuple, Optional, Callable, TypeVar, Generic, Union, Dict, Text

Observation = np.ndarray


class HighwayEnv(AbstractEnv):
    """
    A highway driving environment.

    The vehicle is driving on a straight highway with several lanes, and is rewarded for reaching a high speed,
    staying on the rightmost lanes and avoiding collisions.
    """

    @classmethod
    def default_config(cls) -> dict:
        config = super().default_config()
        config.update({
            "observation": {
                "type": "Kinematics"
            },
            "action": {
                "type": "DiscreteMetaAction",
            },
            "lanes_count": 4,
            "vehicles_count": 50,
            "controlled_vehicles": 1,
            "initial_lane_id": None,
            "duration": 40,  # [s]
            "ego_spacing": 2,
            "vehicles_density": 1,
            # The reward received when colliding with a vehicle.
            "collision_reward": -1,
            # The reward received when driving on the right-most lanes, linearly mapped to
            "right_lane_reward": 0.1,
            # zero for other lanes.
            # The reward received when driving at full speed, linearly mapped to zero for
            "high_speed_reward": 0.4,
            # lower speeds according to config["reward_speed_range"].
            # The reward received at each lane change action.
            "lane_change_reward": 0,
            "reward_speed_range": [20, 30],
            "normalize_reward": True,
            "offroad_terminal": False
        })
        return config

    def _reset(self) -> None:
        self._create_road()
        self._create_vehicles()

    def _create_road(self) -> None:
        """Create a road composed of straight adjacent lanes."""
        self.road = Road(network=RoadNetwork.straight_road_network(self.config["lanes_count"], speed_limit=30),
                         np_random=self.np_random, record_history=self.config["show_trajectories"])

    def _create_vehicles(self) -> None:
        """Create some new random vehicles of a given type, and add them on the road."""
        other_vehicles_type = utils.class_from_path(
            self.config["other_vehicles_type"])
        other_per_controlled = near_split(
            self.config["vehicles_count"], num_bins=self.config["controlled_vehicles"])

        self.controlled_vehicles = []
        for others in other_per_controlled:
            vehicle = Vehicle.create_random(
                self.road,
                speed=25,
                lane_id=self.config["initial_lane_id"],
                spacing=self.config["ego_spacing"]
            )
            vehicle = self.action_type.vehicle_class(
                self.road, vehicle.position, vehicle.heading, vehicle.speed)
            self.controlled_vehicles.append(vehicle)
            self.road.vehicles.append(vehicle)

            for _ in range(others):
                vehicle = other_vehicles_type.create_random(
                    self.road, spacing=1 / self.config["vehicles_density"])
                vehicle.randomize_behavior()
                self.road.vehicles.append(vehicle)

    def _reward(self, action: Action) -> float:
        """
        The reward is defined to foster driving at high speed, on the rightmost lanes, and to avoid collisions.
        :param action: the last action performed
        :return: the corresponding reward
        """
        rewards = self._rewards(action)
        reward = sum(self.config.get(name, 0) *
                     reward for name, reward in rewards.items())
        if self.config["normalize_reward"]:
            reward = utils.lmap(reward,
                                [self.config["collision_reward"],
                                 self.config["high_speed_reward"] + self.config["right_lane_reward"]],
                                [0, 1])
        reward *= rewards['on_road_reward']
        return reward

    def _rewards(self, action: Action) -> Dict[Text, float]:
        neighbours = self.road.network.all_side_lanes(self.vehicle.lane_index)
        lane = self.vehicle.target_lane_index[2] if isinstance(self.vehicle, ControlledVehicle) \
            else self.vehicle.lane_index[2]
        # Use forward speed rather than speed, see https://github.com/eleurent/highway-env/issues/268
        forward_speed = self.vehicle.speed * np.cos(self.vehicle.heading)
        scaled_speed = utils.lmap(
            forward_speed, self.config["reward_speed_range"], [0, 1])
        return {
            "collision_reward": float(self.vehicle.crashed),
            "right_lane_reward": lane / max(len(neighbours) - 1, 1),
            "high_speed_reward": np.clip(scaled_speed, 0, 1),
            "on_road_reward": float(self.vehicle.on_road)
        }

    def _is_terminated(self) -> bool:
        """The episode is over if the ego vehicle crashed."""
        return (self.vehicle.crashed or
                self.config["offroad_terminal"] and not self.vehicle.on_road)

    def _is_truncated(self) -> bool:
        """The episode is truncated if the time limit is reached."""
        return self.time >= self.config["duration"]


class HighwayEnvFast(HighwayEnv):
    """
    A variant of highway-v0 with faster execution:
        - lower simulation frequency
        - fewer vehicles in the scene (and fewer lanes, shorter episode duration)
        - only check collision of controlled vehicles with others
    """
    @classmethod
    def default_config(cls) -> dict:
        cfg = super().default_config()
        cfg.update({
            "simulation_frequency": 5,
            "lanes_count": 3,
            "vehicles_count": 20,
            "duration": 30,  # [s]
            "ego_spacing": 1.5,
        })
        return cfg

    def _create_vehicles(self) -> None:
        super()._create_vehicles()
        # Disable collision check for uncontrolled vehicles
        for vehicle in self.road.vehicles:
            if vehicle not in self.controlled_vehicles:
                vehicle.check_collisions = False


class DevHighway(HighwayEnv):

    def default_config(cls) -> dict:
        config = super().default_config()
        config.update({
            # "action": {"type": "DiscreteMetaAction"},
            "action": {"type": "ContinuousAction"},
            "simulation_frequency": 15,
            "policy_frequency": 15,
            "lanes_count": 3,
            "vehicles_count": 50,
            "duration": 30,  # [s]
            "ego_spacing": 2,
            "lane_centering_cost": 4,
            "lane_centering_reward": 0.01,
            "abrupt_steering_penalty": 0.019,
            "abrupt_acceleration_penalty": 0.001,
            "right_lane_reward": 0.1,
            "high_speed_reward": 0.35,
            "offroad_terminal": False,
        })
        return config

    def _reward(self, action: Action) -> float:
        """
        The reward is defined to foster driving at high speed, on the rightmost lanes, and to avoid collisions.
        :param action: the last action performed
        :return: the corresponding reward
        """
        rewards = self._rewards(action)
        reward = sum(self.config.get(name, 0) *
                     reward for name, reward in rewards.items())
        if self.config["normalize_reward"]:
            reward = utils.lmap(reward,
                                [self.config["collision_reward"],
                                 self.config["high_speed_reward"] + self.config["right_lane_reward"] +
                                 self.config["lane_centering_reward"] + self.config["abrupt_steering_penalty"] +
                                 self.config["abrupt_acceleration_penalty"]],
                                [0, 1])
        reward *= rewards['on_road_reward']
        return reward

    def _rewards(self, action: Action) -> dict[str, float]:
        neighbours = self.road.network.all_side_lanes(self.vehicle.lane_index)
        lane = self.vehicle.target_lane_index[2] if isinstance(self.vehicle, ControlledVehicle) \
            else self.vehicle.lane_index[2]

        # low speed penalty

        # Use forward speed rather than speed, see https://github.com/eleurent/highway-env/issues/268
        forward_speed = self.vehicle.speed * np.cos(self.vehicle.heading)

        # # print vehicle heading
        # print("forward_speed: " + str(forward_speed))

        scaled_speed = utils.lmap(
            forward_speed, self.config["reward_speed_range"], [0, 1])

        # Reward for stay in the lane properly
        _, lateral = self.vehicle.lane.local_coordinates(self.vehicle.position)

        # Calculate the penalty for abrupt steering changes, assuming 'action.steering' is continuous
        steering_change_penalty = self.config["abrupt_steering_penalty"] * abs(
            action[1] - self.previous_steering)
        # Update the previous steering value for the next timestep
        self.previous_steering = action[1]

        # Calculate the penalty for abrupt acceleration changes, assuming 'action.acceleration' is continuous
        acceleration_change_penalty = self.config["abrupt_acceleration_penalty"] * abs(
            action[0] - self.previous_acceleration)
        # Update the previous acceleration value for the next timestep
        self.previous_acceleration = action[0]

        return {
            "collision_reward": -1.0 if self.vehicle.crashed else 0,
            "right_lane_reward": lane / max(len(neighbours) - 1, 1),
            "high_speed_reward": np.clip(scaled_speed, 0, 1),
            "on_road_reward": float(self.vehicle.on_road),
            "lane_centering_reward": 1 / (1 + self.config["lane_centering_cost"] * lateral**2),
            # Negative value to represent penalty
            "abrupt_steering_penalty": -steering_change_penalty,
            # Negative value to represent penalty
            "abrupt_acceleration_penalty": -acceleration_change_penalty
        }

    def lane_distance(self, vehicle):
        indexes, distances = [], []
        for _from, to_dict in vehicle.road.network.graph.items():
            for _to, lanes in to_dict.items():
                for _id, l in enumerate(lanes):
                    distances.append(l.distance_with_heading(
                        vehicle.position, vehicle.heading))
                    indexes.append((_from, _to, _id))
        return distances

    def _reset(self) -> None:
        super()._reset()
        self.previous_steering = 0
        self.previous_acceleration = 0
