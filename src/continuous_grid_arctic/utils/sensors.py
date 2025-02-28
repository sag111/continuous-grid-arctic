from math import pi, degrees, radians, cos, sin, atan, acos, asin, sqrt
import numpy as np
import pygame
from scipy.spatial import distance
from collections import deque
import itertools

try:
    from continuous_grid_arctic.utils.misc import angle_correction, rotateVector, calculateAngle, distance_to_rect, areDotsOnLeft
except:
    from src.continuous_grid_arctic.utils.misc import angle_correction, rotateVector, calculateAngle, distance_to_rect, areDotsOnLeft

import pandas as pd

#TODO: пофиксить корридор так, как я сделал это с 2дкартой по лидару
#TODO: запустить эксперимент где один признак реагирует только на корридор, другой тойько на препятствия

class LaserSensor():
    """
    Implements laser lidar
    The number of rays, the angle between them, the covered sector, and the frequency of “points” are set.
    scan() returns points that are not covered by obstacles, reacts to everything with hitboxes (leader, rocks, bears, river)
    """

    def __init__(self,
                 host_object,
                 sensor_name="LaserSensor",
                 available_angle=360,
                 angle_step=10,  # в градусах
                 points_number=20,  # число пикселей,
                 sensor_range=5,  # в метрах
                 distance_variance=0,
                 angle_variance=0,
                 sensor_speed=0.1,
                 return_all_points=False,
                 add_noise=False,
                 return_only_distances=False
                 ):  # в секундах? Пока не используется

        self.host_object = host_object
        self.return_only_distances = return_only_distances

        self.available_angle = min(360, available_angle)
        self.angle_step = angle_step

        self.range = sensor_range

        self.distance_variance = distance_variance
        self.angle_variance = angle_variance

        self.sensor_speed = sensor_speed
        self.return_all_points = return_all_points
        self.sensed_points = list()
        self.points_number = points_number
        self.data_shape = int(self.available_angle / self.angle_step)
        if self.return_all_points:
            self.data_shape = self.data_shape * points_number  # + 1

    def __len__(self):
        return self.data_shape

    def scan(self, env):
        """
        builds fields of lidar points.

        :param env (Game environment):
           "following the leader" environment

        :return:
            a list of points that the lidar has tracked
        """

        # Если на нужной дистанции нет ни одного объекта - просто рисуем крайние точки, иначе нужно будет идти сложным путём
        objects_in_range = list()

        env_range = self.range * env.PIXELS_TO_METER

        for cur_object in (env.game_object_list + env.game_dynamic_list):
            if cur_object is env.follower:
                continue

            if cur_object.blocks_vision:
                if distance_to_rect(self.host_object.position, cur_object) <= env_range + (3 * env.PIXELS_TO_METER):
                    objects_in_range.append(cur_object)

        # Далее определить, в какой стороне находится объект из списка, и если он входит в область лидара, ставить точку как надо
        # иначе -- просто ставим точку на максимуме
        border_angle = int(self.available_angle / 2)

        x1 = self.host_object.position[0]
        y1 = self.host_object.position[1]

        self.sensed_points = list()
        angles = list()

        cur_angle_diff = 0

        angles.append(-self.host_object.direction)

        while cur_angle_diff < border_angle:
            cur_angle_diff += self.angle_step

            angles.append(angle_correction(-self.host_object.direction + cur_angle_diff))
            angles.append(angle_correction(-self.host_object.direction - cur_angle_diff))

        for angle in angles:

            x2, y2 = (x1 + env_range * cos(radians(angle)), y1 - env_range * sin(radians(angle)))

            point_to_add = None
            object_in_sight = False

            for i in range(0, self.points_number):
                u = i / self.points_number
                cur_point = ((x2 * u + x1 * (1 - u)), (y2 * u + y1 * (1 - u)))

                if self.return_all_points:
                    self.sensed_points.append(cur_point)
                for cur_object in objects_in_range:
                    if cur_object.rectangle.collidepoint(cur_point):
                        point_to_add = np.array(cur_point, dtype=np.float32)
                        object_in_sight = True
                        break

                if object_in_sight:
                    break

            if point_to_add is None:
                point_to_add = np.array((x2, y2), dtype=np.float32)

            if not self.return_all_points:
                self.sensed_points.append(point_to_add)

        if self.return_only_distances:
            return np.linalg.norm(self.sensed_points - self.host_object.position, axis=1)
        else:
            return self.sensed_points - self.host_object.position

    def show(self, env):
        for cur_point in self.sensed_points:
            pygame.draw.circle(env.gameDisplay, (250, 50, 50), cur_point, 3)

    # @staticmethod
    # def _add_noise(val, variance):
    #    return max(np.random.normal(val, variance), 0)


class LeaderPositionsTracker:
    """
    Deprecated class, it is recommended to use LeaderPositionsTracker_v2
    Class that tracks observed leader positions.
    does not generate observations, but stores a history of leader positions for other sensors.
    TODO: Может имеет смысл переделать на относительные координаты, это же ведомый отслеживает относительно себя, но тогда другие сенсоры тоже надо фиксить.
    """

    def __init__(self,
                 host_object,
                 sensor_name="LeaderPositionsTracker",
                 eat_close_points=True,
                 max_point=5000,
                 saving_period=5,
                 generate_corridor=True,
                 start_corridor_behind_follower=False
                 ):
        self.host_object = host_object
        self.max_point = max_point
        self.eat_close_points = eat_close_points
        # TODO: попробовать реализовать как ndarray, может быстрее будет, потому что другие сенсоры это как ndarray используют
        self.leader_positions_hist = deque()
        self.saving_period = saving_period
        self.saving_counter = 0
        self.generate_corridor = generate_corridor
        self.corridor = deque()
        self.right_border_dot = np.array([0, 0])
        self.left_border_dot = np.array([0, 0])
        self.start_corridor_behind_follower = start_corridor_behind_follower

    def scan(self, env):
        # если сам сенсор отслеживает перемещение
        if self.saving_counter % self.saving_period == 0:
            # Если позиция лидера не изменилась с последнего обсерва, просто возвращаем, что есть, ничего не обновляем
            if len(self.leader_positions_hist) > 0 and (self.leader_positions_hist[-1] == env.leader.position).all():
                if self.generate_corridor:
                    return self.leader_positions_hist, self.corridor
                else:
                    return self.leader_positions_hist
            # Если симуляция только началась, сохраняем текущую ведомого, чтоб начать от неё строить коридор
            if len(self.leader_positions_hist) == 0:
                self.leader_positions_hist.append(self.host_object.position.copy())
            self.leader_positions_hist.append(env.leader.position.copy())
            if self.generate_corridor and len(self.leader_positions_hist) > 1:
                last_2points_vec = self.leader_positions_hist[-1] - self.leader_positions_hist[-2]
                last_2points_vec *= env.max_dev / np.linalg.norm(last_2points_vec)
                right_border_dot = rotateVector(last_2points_vec, 90)
                right_border_dot += self.leader_positions_hist[-2]
                left_border_dot = rotateVector(last_2points_vec, -90)
                left_border_dot += self.leader_positions_hist[-2]
                self.corridor.append([right_border_dot, left_border_dot])
        # Можно ещё брать из среды, но там частота сохранения другая
        # self.leader_positions_hist = env.leader_factual_trajectory[::self.saving_period]
        # assert env.frames_per_step % env.trajectory_saving_period == 0
        # print(self.leader_positions_hist)
        # print(env.leader_factual_trajectory[::self.saving_period*(int(env.frames_per_step / env.trajectory_saving_period))])

        self.saving_counter += 1

        if self.eat_close_points and len(self.leader_positions_hist) > 0:
            norms = np.linalg.norm(np.array(self.leader_positions_hist) - self.host_object.position, axis=1)
            indexes = np.nonzero(norms <= max(self.host_object.width, self.host_object.height))[0]
            for index in sorted(indexes, reverse=True):
                del self.leader_positions_hist[index]
        if self.generate_corridor:
            return self.leader_positions_hist, self.corridor
        else:
            return self.leader_positions_hist

    def reset(self):
        self.leader_positions_hist.clear()
        self.corridor.clear()
        self.saving_counter = 0

    def show(self, env):
        for point in self.leader_positions_hist:
            pygame.draw.circle(env.gameDisplay, (50, 10, 10), point, 3)

        if len(self.corridor) > 1:
            pygame.draw.lines(env.gameDisplay, (150, 120, 50), False, [x[0] for x in self.corridor], 3)
            pygame.draw.lines(env.gameDisplay, (150, 120, 50), False, [x[1] for x in self.corridor], 3)
        pass


class LeaderPositionsTracker_v2(LeaderPositionsTracker):
    """
    It differs from the first version in that at the beginning of the simulation the corridor is not built along
    2 points (leader, follower), and a sequence of points between them is created. + can start this corridor behind
    the follower’s back
    """
    def __init__(self, *args, corridor_width, corridor_length, **kwargs):
        self.corridor_width = corridor_width
        self.corridor_length = corridor_length
        super(LeaderPositionsTracker_v2, self).__init__(*args, **kwargs)

    def scan(self, env):
        # если сам сенсор отслеживает перемещение
        if self.saving_counter % self.saving_period == 0:
            # Если позиция лидера не изменилась с последнего обсерва, просто возвращаем, что есть, ничего не обновляем
            if len(self.leader_positions_hist) > 0 and (self.leader_positions_hist[-1] == env.leader.position).all():
                if self.generate_corridor:
                    return self.leader_positions_hist, self.corridor
                else:
                    return self.leader_positions_hist
            # Если симуляция только началась, сохраняем текущую ведомого, чтоб начать от неё строить коридор
            if len(self.leader_positions_hist) == 0 and self.saving_counter == 0:
                # self.leader_positions_hist.append(self.host_object.position.copy())
                # TODO ; точка позиции за ведомым (для отстроения коридора)
                # TODO : вариант с отсроением коридора от точки за метр от ведомого
                if self.start_corridor_behind_follower:
                    point_start_distance_behind_follower = 50
                    point_start_position_theta = angle_correction(self.host_object.direction + 180)
                    point_behind_follower = np.array(
                        (point_start_distance_behind_follower * cos(radians(point_start_position_theta)),
                         point_start_distance_behind_follower * sin(radians(point_start_position_theta)))) \
                                            + self.host_object.position
                    first_dots_for_follower_count = int(
                        distance.euclidean(point_behind_follower, env.leader.position) / (
                                self.saving_period * 5 * env.leader.max_speed))

                    self.leader_positions_hist.extend(np.array(x) for x in
                                                      zip(np.linspace(point_behind_follower[0], env.leader.position[0],
                                                                      first_dots_for_follower_count),
                                                          np.linspace(point_behind_follower[1], env.leader.position[1],
                                                                      first_dots_for_follower_count)))
                # TODO : вариант с отсроением коридора от точки ведомого
                else:
                    first_dots_for_follower_count = int(
                        distance.euclidean(self.host_object.position, env.leader.position) / (
                                self.saving_period * 5 * env.leader.max_speed))
                    self.leader_positions_hist.extend(np.array(x) for x in
                                                      zip(np.linspace(self.host_object.position[0],
                                                                      env.leader.position[0],
                                                                      first_dots_for_follower_count),
                                                          np.linspace(self.host_object.position[1],
                                                                      env.leader.position[1],
                                                                      first_dots_for_follower_count)))
            else:
                self.leader_positions_hist.append(env.leader.position.copy())

            dists = np.linalg.norm(np.array(self.leader_positions_hist)[:-1, :] -
                                   np.array(self.leader_positions_hist)[1:, :], axis=1)
            path_length = np.sum(dists)
            # while path_length > env.max_distance:
            while path_length > self.corridor_length:
                self.leader_positions_hist.popleft()
                self.corridor.popleft()
                dists = np.linalg.norm(np.array(self.leader_positions_hist)[:-1, :] -
                                       np.array(self.leader_positions_hist)[1:, :], axis=1)
                path_length = np.sum(dists)

            if self.generate_corridor and len(self.leader_positions_hist) > 1:
                if self.saving_counter == 0:
                    for i in range(len(self.leader_positions_hist) - 1, 0, -1):
                        last_2points_vec = self.leader_positions_hist[i] - self.leader_positions_hist[i - 1]
                        # last_2points_vec *= env.max_dev / np.linalg.norm(last_2points_vec)
                        last_2points_vec *= self.corridor_width / np.linalg.norm(last_2points_vec)
                        right_border_dot = rotateVector(last_2points_vec, 90)
                        right_border_dot += self.leader_positions_hist[-i - 1]
                        left_border_dot = rotateVector(last_2points_vec, -90)
                        left_border_dot += self.leader_positions_hist[-i - 1]
                        self.corridor.append([right_border_dot, left_border_dot])
                last_2points_vec = self.leader_positions_hist[-1] - self.leader_positions_hist[-2]
                # last_2points_vec *= env.max_dev / np.linalg.norm(last_2points_vec)
                last_2points_vec *= self.corridor_width / np.linalg.norm(last_2points_vec)
                right_border_dot = rotateVector(last_2points_vec, 90)
                right_border_dot += self.leader_positions_hist[-2]
                left_border_dot = rotateVector(last_2points_vec, -90)
                left_border_dot += self.leader_positions_hist[-2]
                self.corridor.append([right_border_dot, left_border_dot])
        # Можно ещё брать из среды, но там частота сохранения другая
        # self.leader_positions_hist = env.leader_factual_trajectory[::self.saving_period]
        # assert env.frames_per_step % env.trajectory_saving_period == 0
        # print(self.leader_positions_hist)
        # print(env.leader_factual_trajectory[::self.saving_period*(int(env.frames_per_step / env.trajectory_saving_period))])
        self.saving_counter += 1
        if self.generate_corridor:
            return self.leader_positions_hist, self.corridor
        else:
            return self.leader_positions_hist

    def show(self, env):
        for point in self.leader_positions_hist:
            pygame.draw.circle(env.gameDisplay, (80, 10, 10), point, 3)

        # показывать или не показывать границы коридора

        if len(self.corridor) > 1:
            pygame.draw.lines(env.gameDisplay, (150, 120, 50), False, [x[0] for x in self.corridor], 3)
            pygame.draw.lines(env.gameDisplay, (150, 120, 50), False, [x[1] for x in self.corridor], 3)
            pygame.draw.line(env.gameDisplay, (150, 120, 50), self.corridor[0][0], self.corridor[0][1], 3)
            pygame.draw.line(env.gameDisplay, (150, 120, 50), self.corridor[-1][0], self.corridor[-1][1], 3)


class LeaderTrackDetector_vector:
    """
    Class that reacts to old leader positions and generates vectors to certain positions.
    can track the newest leader positions or the oldest
    TODO: Добавить вариант отслеживания позиций или радара до ближайших точек до преследователя
    """

    def __init__(self,
                 host_object,
                 sensor_name="LeaderTrackDetector_vector",
                 position_sequence_length=100,
                 detectable_positions="new"):
        """
        :param host_object:
            the agent
        :param position_sequence_length:
            length of the sequence to be used by the radar
        """
        self.host_object = host_object
        self.position_sequence_length = position_sequence_length
        self.vecs_values = np.zeros((self.position_sequence_length, 2), dtype=np.float32)
        self.detectable_positions = detectable_positions

    def scan(self, env, leader_positions_hist):
        self.vecs_values = np.zeros((self.position_sequence_length, 2), dtype=np.float32)
        if len(leader_positions_hist) > 0:
            if self.detectable_positions == "new":
                # vecs = np.array(leader_positions_hist[-self.position_sequence_length:]) - self.host_object.position
                slice = list(itertools.islice(leader_positions_hist, max(0, len(leader_positions_hist) -
                                                                         self.position_sequence_length),
                                              len(leader_positions_hist)))
                vecs = np.array(slice) - self.host_object.position
            elif self.detectable_positions == "old":
                # vecs = np.array(leader_positions_hist[:self.position_sequence_length]) - self.host_object.position
                slice = list(itertools.islice(leader_positions_hist, 0, self.position_sequence_length))
                vecs = np.array(slice) - self.host_object.position
            self.vecs_values[
            :min(len(leader_positions_hist), self.position_sequence_length)] = vecs
        return self.vecs_values

    def show(self, env):
        for i in range(self.vecs_values.shape[0]):
            if np.sum(self.host_object.position + self.vecs_values[i]) > 0:
                # pygame.draw.line(self.gameDisplay, (250, 200, 150), self.follower.position, \
                # self.follower.position+self.follower.sensors["ObservedLeaderPositions_packmanStyle"].vecs_values[i])
                pygame.draw.circle(env.gameDisplay, (255, 100, 50), self.host_object.position +
                                   self.vecs_values[i], 1)

    def reset(self):
        self.vecs_values = np.zeros((self.position_sequence_length, 2), dtype=np.float32)


class LeaderTrackDetector_radar:
    """
    Radar that reacts to old leader positions and indicates if there are leader positions in
    certain sectors of the semicircle in front of the pursuer
    can track the newest leader positions or the oldest
    TODO: Добавить вариант отслеживания позиций или радара до ближайших точек до преследователя
    """

    def __init__(self,
                 host_object,
                 sensor_name="LeaderTrackDetector_radar",
                 position_sequence_length=100,
                 detectable_positions="old",
                 radar_sectors_number=180):
        """
        :param host_object:
            the agent
        :param position_sequence_length:
            length of the sequence to be used by the radar
        :param radar_sectors_number:
            number of sectors in the radar
        """
        self.host_object = host_object
        self.detectable_positions = detectable_positions
        self.position_sequence_length = position_sequence_length
        self.radar_sectors_number = radar_sectors_number
        self.sectorsAngle_rad = np.pi / radar_sectors_number
        self.sectorsAngle_deg = 180 / radar_sectors_number
        self.radar_values = np.zeros(self.radar_sectors_number, dtype=np.float32)

    def scan(self, env, leader_positions_hist):
        followerDirVec = rotateVector(np.array([1, 0]), self.host_object.direction)
        followerRightDir = self.host_object.direction + 90
        if followerRightDir >= 360:
            followerRightDir -= 360
        followerRightVec = rotateVector(np.array([1, 0]), followerRightDir)
        """
        distances_follower_to_leadhistory = np.linalg.norm(vecs_follower_to_leadhistory, axis=1)
        angles_history_to_dir = calculateAngle(np.array([self.leader.position-self.follower.position, self.leader.position, self.follower.position]), followerDirVec)
        angles_history_to_right = calculateAngle(np.array([self.leader.position-self.follower.position, self.leader.position, self.follower.position]), followerRightVec)
        """
        self.radar_values = np.zeros(self.radar_sectors_number, dtype=np.float32)
        if len(leader_positions_hist) > 0:

            if self.detectable_positions == "near":
                leader_positions_hist = np.array(leader_positions_hist)
                vecs_follower_to_leadhistory = leader_positions_hist - self.host_object.position
                distances_follower_to_chosenDots = np.linalg.norm(vecs_follower_to_leadhistory, axis=1)
                closest_indexes = np.argsort(distances_follower_to_chosenDots)
                vecs_follower_to_leadhistory = vecs_follower_to_leadhistory[closest_indexes]
                distances_follower_to_chosenDots = distances_follower_to_chosenDots[closest_indexes]
            else:
                if self.detectable_positions == "new":
                    # chosen_dots = np.array(leader_positions_hist[-self.position_sequence_length:])
                    slice = list(itertools.islice(leader_positions_hist, max(0, len(leader_positions_hist) -
                                                                             self.position_sequence_length),
                                                  len(leader_positions_hist)))
                    chosen_dots = np.array(slice)
                elif self.detectable_positions == "old":
                    # chosen_dots = np.array(leader_positions_hist[:self.position_sequence_length])
                    slice = list(itertools.islice(leader_positions_hist, 0, self.position_sequence_length))
                    chosen_dots = np.array(slice)
                vecs_follower_to_leadhistory = chosen_dots - self.host_object.position
                distances_follower_to_chosenDots = np.linalg.norm(vecs_follower_to_leadhistory, axis=1)
            angles_history_to_dir = calculateAngle(vecs_follower_to_leadhistory, followerDirVec)
            angles_history_to_right = calculateAngle(vecs_follower_to_leadhistory, followerRightVec)
            angles_history_to_right[angles_history_to_dir > np.pi / 2] = -angles_history_to_right[
                angles_history_to_dir > np.pi / 2]
            for i in range(self.radar_sectors_number):
                sector_dots_distances = distances_follower_to_chosenDots[
                    (angles_history_to_right >= self.sectorsAngle_rad * i) & (
                            angles_history_to_right < self.sectorsAngle_rad * (i + 1))]
                if len(sector_dots_distances) > 0:
                    self.radar_values[i] = np.min(sector_dots_distances)
        return self.radar_values

    def reset(self):
        self.radar_values = np.zeros(self.radar_sectors_number, dtype=np.float32)

    def show(self, env):
        for i in range(self.radar_values.shape[0]):
            followerRightDir = self.host_object.direction + 90
            if followerRightDir >= 360:
                followerRightDir -= 360

            for i in range(self.radar_sectors_number):
                if self.radar_values[i] == 0:
                    continue
                followerRightVec = rotateVector(np.array([self.radar_values[i], 0]), followerRightDir)
                relativeDot = rotateVector(followerRightVec, self.sectorsAngle_deg * (self.radar_sectors_number - i) - (
                        self.sectorsAngle_deg / 2))
                absDot = self.host_object.position - relativeDot
                pygame.draw.line(env.gameDisplay, (180, 80, 255), self.host_object.position, absDot, 3)
                # pygame.draw.circle(env.gameDisplay, (255, 80, 180), absDot, 4)


class GreenBoxBorderSensor(LaserSensor):
    """Lidar for tracking the boundaries of the green zone in which the leader should be."""

    def __init__(self, host_object, **kwargs):
        raise ValueError("To use it, you need to uncomment the call self._get_green_zone_border_points("
                         "). Commented out because it slows down the simulation")

        super().__init__(host_object, **kwargs)

    def scan(self, env):
        """
        builds fields of lidar points

        :param env (Game environment):
            the environment in which scanning is carried out

        :return:
            a list of points that the lidar has tracked
        """

        # Далее определить, в какой стороне находится объект из списка, и если он входит в область лидара, ставить точку как надо
        # иначе -- просто ставим точку на максимуме

        env_range = self.range * env.PIXELS_TO_METER

        border_angle = int(self.available_angle / 2)

        x1 = self.host_object.position[0]
        y1 = self.host_object.position[1]

        self.sensed_points = list()
        angles = list()

        cur_angle_diff = 0

        angles.append(-self.host_object.direction)

        while cur_angle_diff < border_angle:
            cur_angle_diff += self.angle_step

            angles.append(angle_correction(-self.host_object.direction + cur_angle_diff))
            angles.append(angle_correction(-self.host_object.direction - cur_angle_diff))

        list_points_to_scan = env.left_border_points_list + env.right_border_points_list

        for angle in angles:

            x2, y2 = (x1 + env_range * cos(radians(angle)), y1 - env_range * sin(radians(angle)))

            point_to_add = None
            object_in_sight = False

            for i in range(0, self.points_number):
                u = i / self.points_number
                cur_point = ((x2 * u + x1 * (1 - u)), (y2 * u + y1 * (1 - u)))

                if self.return_all_points:
                    self.sensed_points.append(cur_point)

                for cur_point_to_scan in list_points_to_scan:
                    if distance.euclidean(cur_point_to_scan, cur_point) <= 5:
                        point_to_add = np.array(cur_point, dtype=np.float32)
                        object_in_sight = True
                        break

                if object_in_sight:
                    break

            if point_to_add is None:
                point_to_add = np.array((x2, y2), dtype=np.float32)

            if not self.return_all_points:
                self.sensed_points.append(point_to_add)

        return self.sensed_points

    def show(self, env):
        for cur_point in self.sensed_points:
            pygame.draw.circle(env.gameDisplay, env.colours["blue"], cur_point, 2)


class LeaderCorridor_lasers:
    """
    Sign "cockroach" - several lasers in different directions, which are checked for crossing borders
    route corridor safe, zone and obstacle safe. This version has a fixed number of lasers: 3 in front,
    optional 2 sides and 2 rear
    """
    def __init__(self,
                 host_object,
                 sensor_name="LeaderCorridor_lasers",
                 react_to_safe_corridor=True,
                 react_to_obstacles=False,
                 react_to_green_zone=False,
                 front_lasers_count=3,
                 back_lasers_count=0,
                 laser_length=100):
        """

        :param host_object:
            robot with sensor hanging on it
        :param react_to_obstacles:
            whether lasers should react to obstacles. can have the values True,False,"dynamic","static"
        :param react_to_green_zone:
            should lasers respond to the front of the back of the green zone
        """
        self.host_object = host_object
        # TODO: сделать гибкую настройку лазеров
        assert front_lasers_count in [3, 5]
        assert back_lasers_count in [0, 2]
        self.front_lasers_count = front_lasers_count
        self.back_lasers_count = back_lasers_count
        self.laser_length = laser_length
        self.lasers_end_points = []
        self.lasers_collides = []
        self.react_to_safe_corridor = react_to_safe_corridor
        self.react_to_obstacles = react_to_obstacles
        self.react_to_green_zone = react_to_green_zone

    def ccw(A, B, C):
        return (C[:, 1] - A[:, 1]) * (B[:, 0] - A[:, 0]) > (B[:, 1] - A[:, 1]) * (C[:, 0] - A[:, 0])

    # Return true if line segments AB and CD intersect
    def intersect(A, B, C, D):
        return np.logical_and(LeaderCorridor_lasers.ccw(A, C, D) != LeaderCorridor_lasers.ccw(B, C, D),
                              LeaderCorridor_lasers.ccw(A, B, C) != LeaderCorridor_lasers.ccw(A, B, D))

    def perp(a):
        # https://stackoverflow.com/a/3252222/4807259
        b = np.empty_like(a)
        b[:, 0] = -a[:, 1]
        b[:, 1] = a[:, 0]
        return b

    # line segment a given by endpoints a1, a2
    # line segment b given by endpoints b1, b2
    # return
    def seg_intersect(a1, a2, b1, b2):
        # https://stackoverflow.com/a/3252222/4807259
        # ДОБАВИТЬ ДЛЯ КОЛЛИНЕАРНОСТИ  УСЛОВИЕ, ЧТОБ УКАЗЫВАТЬ БЛИЖАЙШИЙ КОНЕЦ КАК ТОЧКУ ПЕРЕСЕЧЕНИЯ
        da = a2 - a1
        db = b2 - b1
        dp = a1 - b1
        dap = LeaderCorridor_lasers.perp(da)
        denom = np.dot(dap, db.transpose())

        # num = np.zeros(dp.shape[0])
        # а можно ли как-то без цикла?
        # for i in range(dp.shape[0]):
        # num[i] = dot( dap[i,:], dp[i,:] )
        num = np.sum(np.multiply(dap, dp), axis=1)
        return (num[:, np.newaxis] / denom) * db + b1

    def collect_obstacle_edges(self, env, corridor):
        obstacle_lines = list()
        if self.react_to_safe_corridor:
            for i in range(len(corridor) - 1):
                obstacle_lines.append([corridor[i][0], corridor[i + 1][0]])
                obstacle_lines.append([corridor[i][1], corridor[i + 1][1]])
        if self.react_to_green_zone:
            obstacle_lines.append([corridor[0][0], corridor[0][1]])
            obstacle_lines.append([corridor[-1][0], corridor[-1][1]])
        if self.react_to_obstacles != False:
            # TODO : проверка списка динам препятствий
            if self.react_to_obstacles == "dynamic":
                obstacles_list = env.game_dynamic_list
            elif self.react_to_obstacles == "static":
                obstacles_list = env.game_object_list
            elif self.react_to_obstacles == "all":
                obstacles_list = env.game_object_list + env.game_dynamic_list
            elif type(self.react_to_obstacles) == bool and self.react_to_obstacles:
                obstacles_list = env.game_object_list + env.game_dynamic_list
            else:
                ValueError("You need to specify which obstacles the sensor should respond to. Set "
                           "react_to_obstacles equal to one of the values: True, 'all', 'dynamic', 'static'")
            for cur_object in (obstacles_list):
                if cur_object is env.follower:
                    continue
                if cur_object.blocks_vision:
                    obstacle_lines.append([cur_object.rectangle.bottomleft, cur_object.rectangle.bottomright])
                    obstacle_lines.append([cur_object.rectangle.topright, cur_object.rectangle.bottomright])
                    obstacle_lines.append([cur_object.rectangle.topright, cur_object.rectangle.topleft])
                    obstacle_lines.append([cur_object.rectangle.bottomleft, cur_object.rectangle.topleft])
        obstacle_lines = np.array(obstacle_lines, dtype=np.float32)
        return obstacle_lines

    def scan(self, env, corridor):
        self.lasers_collides = []
        self.lasers_end_points = []
        self.lasers_end_points.append(
            self.host_object.position + rotateVector(np.array([self.laser_length, 0]), self.host_object.direction - 40))
        self.lasers_end_points.append(
            self.host_object.position + rotateVector(np.array([self.laser_length, 0]), self.host_object.direction))
        self.lasers_end_points.append(
            self.host_object.position + rotateVector(np.array([self.laser_length, 0]), self.host_object.direction + 40))
        if self.front_lasers_count == 5:
            self.lasers_end_points.append(
                self.host_object.position + rotateVector(np.array([self.laser_length, 0]),
                                                         self.host_object.direction - 90))
            self.lasers_end_points.append(
                self.host_object.position + rotateVector(np.array([self.laser_length, 0]),
                                                         self.host_object.direction + 90))
        if self.back_lasers_count == 2:
            self.lasers_end_points.append(
                self.host_object.position + rotateVector(np.array([self.laser_length, 0]),
                                                         self.host_object.direction - 150))
            self.lasers_end_points.append(
                self.host_object.position + rotateVector(np.array([self.laser_length, 0]),
                                                         self.host_object.direction + 150))
        if len(corridor) > 1:
            corridor_lines = self.collect_obstacle_edges(env, corridor)
            lasers_values = []
            self.lasers_collides = []
            for laser_end_point in self.lasers_end_points:
                # эта функция не работает с коллинеарными
                # есть вариант лучше, но медленней
                # https://www.geeksforgeeks.org/check-if-two-given-line-segments-intersect/
                rez = LeaderCorridor_lasers.intersect(corridor_lines[:, 0, :], corridor_lines[:, 1, :],
                                                      np.array([self.host_object.position]),
                                                      np.array([laser_end_point]))
                intersected_line = corridor_lines[rez]
                if len(intersected_line) > 0:
                    x = LeaderCorridor_lasers.seg_intersect(intersected_line[:, 0, :], intersected_line[:, 1, :],
                                                            np.array([self.host_object.position]),
                                                            np.array([laser_end_point]))
                    # TODO: исключить коллинеарные, вместо их точек пересечения добавить ближайшую точку коллинеарной границы
                    # но это бесполезно при использовании функции intersect, которая не работает с коллинеарными
                    exclude_rows = np.concatenate([np.nonzero(np.isinf(x))[0], np.nonzero(np.isnan(x))[0]])
                    norms = np.linalg.norm(x - self.host_object.position, axis=1)
                    lasers_values.append(np.min(norms))
                    closest_dot_idx = np.argmin(np.linalg.norm(x - self.host_object.position, axis=1))
                    self.lasers_collides.append(x[closest_dot_idx])
                else:
                    self.lasers_collides.append(laser_end_point)
        obs = np.ones(self.front_lasers_count + self.back_lasers_count, dtype=np.float32) * self.laser_length
        for i, collide in enumerate(self.lasers_collides):
            obs[i] = np.linalg.norm(collide - self.host_object.position)
        return obs

    def show(self, env):
        for laser_end_point in self.lasers_end_points:
            pygame.draw.line(env.gameDisplay, (200, 0, 100), self.host_object.position, laser_end_point)

        for laser_collide in self.lasers_collides:
            pygame.draw.circle(env.gameDisplay, (200, 0, 100), laser_collide, 5)


class LeaderCorridor_lasers_v2(LeaderCorridor_lasers):
    """
    It differs from LeaderCorridor_lasers in that the number of lasers is flexibly set here.
    They simply starting from the zero angle (probably the first ray goes directly in front of the agent) are
    created in a circle, filling 360 degrees.
    """
    def __init__(self,
                 host_object,
                 sensor_name="LeaderCorridor_lasers",
                 react_to_safe_corridor=True,
                 react_to_obstacles=False,
                 react_to_green_zone=False,
                 lasers_count=12,
                 laser_length=100):
        """

        :param host_object:
            robot with sensor hanging on it
        :param react_to_obstacles:
            whether lasers should react to obstacles. can have the values True,False,"dynamic","static"
        :param react_to_green_zone:
            should lasers respond to the front of the back of the green zone
        """
        self.host_object = host_object
        self.lasers_count = lasers_count
        if self.lasers_count not in [12, 24, 20, 36]:
            raise ValueError("Invalid number of laser beams, should be 12,24,20 or 36")
        self.laser_period = 360 / lasers_count
        self.laser_length = laser_length
        self.lasers_end_points = []
        self.lasers_collides = []
        self.react_to_safe_corridor = react_to_safe_corridor
        self.react_to_obstacles = react_to_obstacles
        self.react_to_green_zone = react_to_green_zone

    def scan(self, env, corridor):
        self.lasers_collides = []
        self.lasers_end_points = []

        for i in range(self.lasers_count):
            self.lasers_end_points.append(self.host_object.position + rotateVector(np.array([self.laser_length, 0]),
                                                                                   self.host_object.direction + i * self.laser_period))

        if len(corridor) > 1:
            corridor_lines = self.collect_obstacle_edges(env, corridor)
            lasers_values = []
            self.lasers_collides = []
            for laser_end_point in self.lasers_end_points:
                # эта функция не работает с коллинеарными
                # есть вариант лучше, но медленней
                # https://www.geeksforgeeks.org/check-if-two-given-line-segments-intersect/
                rez = LeaderCorridor_lasers.intersect(corridor_lines[:, 0, :], corridor_lines[:, 1, :],
                                                      np.array([self.host_object.position]),
                                                      np.array([laser_end_point]))
                intersected_line = corridor_lines[rez]
                if len(intersected_line) > 0:
                    x = LeaderCorridor_lasers.seg_intersect(intersected_line[:, 0, :], intersected_line[:, 1, :],
                                                            np.array([self.host_object.position]),
                                                            np.array([laser_end_point]))
                    # TODO: исключить коллинеарные, вместо их точек пересечения добавить ближайшую точку коллинеарной границы
                    # но это бесполезно при использовании функции intersect, которая не работает с коллинеарными
                    exclude_rows = np.concatenate([np.nonzero(np.isinf(x))[0], np.nonzero(np.isnan(x))[0]])
                    norms = np.linalg.norm(x - self.host_object.position, axis=1)
                    lasers_values.append(np.min(norms))
                    closest_dot_idx = np.argmin(np.linalg.norm(x - self.host_object.position, axis=1))
                    self.lasers_collides.append(x[closest_dot_idx])
                else:
                    self.lasers_collides.append(laser_end_point)
        obs = np.ones(self.lasers_count, dtype=np.float32) * self.laser_length
        for i, collide in enumerate(self.lasers_collides):
            obs[i] = np.linalg.norm(collide - self.host_object.position)
        return obs


class LeaderObstacles_lasers(LeaderCorridor_lasers_v2):
    def __init__(self):
        raise ValueError("Deprecated class, use LeaderCorridor_lasers_v2 with flags instead"
                         "react_to_safe_corridor=False and react_to_green_zone=False")


class Leader_Dyn_Obstacles_lasers(LeaderCorridor_lasers_v2):
    def __init__(self):
        raise ValueError("Deprecated class, use LeaderCorridor_lasers_v2 with flags instead"
                         "react_to_safe_corridor=False and react_to_green_zone=False, react_to_obstacles='dynamic'")


class FollowerInfo:
    def __init__(self,
                 host_object,
                 sensor_name="FollowerInfo",
                 speed_direction_param=2):
        """
        :param host_object:
            robot with sensor hanging on it
        """
        self.host_object = host_object
        self.speed_direction_param = speed_direction_param

    def scan(self, env):
        self.host_object.speed
        self.host_object.direction

        obs = np.ones(self.speed_direction_param, dtype=np.float32)
        obs[0] = self.host_object.speed / self.host_object.max_speed
        obs[1] = self.host_object.direction / 360
        # print(obs)
        return obs

    def show(self, env):
        return 0

class LaserPrevSensor(LeaderCorridor_lasers_v2):
    def __init__(self):
        raise TypeError("This is an obsolete class, you should use LeaderCorridor_Prev_lasers_v2 with flags instead"
                        "react_to_safe_corridor=False and react_to_green_zone=False, react_to_obstacles=True and "
                        "first_laser_angle_offset=0")

class LeaderCorridor_Prev_lasers_v2_compas(LeaderCorridor_lasers_v2):
    def __init__(self):
        raise TypeError("This is an obsolete class, you should use it instead LeaderCorridor_Prev_lasers_v2 "
                        "with flags react_to_safe_corridor=True and react_to_green_zone=True, react_to_obstacles=True")

class LaserPrevSensor_compas(LeaderCorridor_lasers_v2):
    def __init__(self):
        raise TypeError("This is an obsolete class, you should use it instead LeaderCorridor_Prev_lasers_v2 "
                        "with flags react_to_safe_corridor=False and react_to_green_zone=False, react_to_obstacles=True")

#######################################
#### add compas in this sensors
#######################################

class LeaderCorridor_Prev_lasers_v2(LeaderCorridor_lasers_v2):
    """
    It differs from LeaderCorridor_lasers_v2 in that it stores the history of obstacle positions and the lasers
    react not only to the current position of obstacle boundaries, but also to old ones.
    """

    def __init__(self, *args, use_prev_obs=False, max_prev_obs=0, pad_sectors=True, first_laser_angle_offset=-45, **kwargs):
        super(LeaderCorridor_Prev_lasers_v2, self).__init__(*args, **kwargs)
        self.use_prev_obs = use_prev_obs
        assert max_prev_obs > 0
        self.max_prev_obs = max_prev_obs
        self.pad_sectors = pad_sectors
        self.first_laser_angle_offset = first_laser_angle_offset
        self.reset()
        self.lasers_collides_item_history = []

    def scan(self, env, corridor):
        self.lasers_collides = []
        self.lasers_end_points = []
        self.lasers_collides_item_history = []

        for i in range(self.lasers_count):
            self.lasers_end_points.append(self.host_object.position + rotateVector(np.array([self.laser_length, 0]),
                                                                                   (self.host_object.direction + self.first_laser_angle_offset) +
                                                                                   i * self.laser_period))
        # TODO: Исправить, не работает проверка на столкновение лазеров с препятствиями, если пустой коридор
        if len(corridor) > 1:
            corridor_lines = self.collect_obstacle_edges(env, corridor)
            # Сейчас в истории хранятся все грани препятствий, а не только пересеченные
            self.history_obstacles_list.pop(0)
            self.history_obstacles_list.append(corridor_lines)

            all_obs_list = []

            for j, corridor_lines_item in enumerate(self.history_obstacles_list):

                corridor_lines_item = np.array(corridor_lines_item)

                lasers_values_item = []
                self.lasers_collides_item = []
                for laser_end_point in self.lasers_end_points:
                    rez = LeaderCorridor_lasers.intersect(corridor_lines_item[:, 0, :], corridor_lines_item[:, 1, :],
                                                          np.array([self.host_object.position]),
                                                          np.array([laser_end_point]))
                    intersected_line_item = corridor_lines_item[rez]
                    if len(intersected_line_item) > 0:
                        x = LeaderCorridor_lasers.seg_intersect(intersected_line_item[:, 0, :],
                                                                intersected_line_item[:, 1, :],
                                                                np.array([self.host_object.position]),
                                                                np.array([laser_end_point]))
                        # TODO: исключить коллинеарные, вместо их точек пересечения добавить ближайшую точку коллинеарной границы
                        # но это бесполезно при использовании функции intersect, которая не работает с коллинеарными
                        exclude_rows = np.concatenate([np.nonzero(np.isinf(x))[0], np.nonzero(np.isnan(x))[0]])
                        norms = np.linalg.norm(x - self.host_object.position, axis=1)
                        lasers_values_item.append(np.min(norms))
                        closest_dot_idx = np.argmin(np.linalg.norm(x - self.host_object.position, axis=1))
                        self.lasers_collides_item.append(x[closest_dot_idx])
                    else:
                        self.lasers_collides_item.append(laser_end_point)
                self.lasers_collides_item_history.append(self.lasers_collides_item.copy())

                obs_item = np.ones(self.lasers_count, dtype=np.float32) * self.laser_length
                for i, collide in enumerate(self.lasers_collides_item):
                    obs_item[i] = np.linalg.norm(collide - self.host_object.position)

                if self.pad_sectors:
                    front = np.zeros(len(obs_item))
                    right = np.zeros(len(obs_item))
                    behind = np.zeros(len(obs_item))
                    left = np.zeros(len(obs_item))

                    lasers_in_sector = self.lasers_count / 4
                    for i in range(len(obs_item)):
                        if i < lasers_in_sector:
                            front[i] = obs_item[i]
                        elif lasers_in_sector <= i < 2 * lasers_in_sector:
                            right[i] = obs_item[i]
                        elif 2 * lasers_in_sector <= i < 3 * lasers_in_sector:
                            behind[i] = obs_item[i]
                        else:
                            left[i] = obs_item[i]

                    # front = np.array([obs_item[0], obs_item[1], 0, 0, 0, 0, 0, 0, 0, 0, 0, obs_item[11]])
                    # right = np.array([0, 0, obs_item[2], obs_item[3], obs_item[4], 0, 0, 0, 0, 0, 0, 0])
                    # behind = np.array([0, 0, 0, 0, 0, obs_item[5], obs_item[6], obs_item[7], 0, 0, 0, 0])
                    # left = np.array([0, 0, 0, 0, 0, 0, 0, 0, obs_item[8], obs_item[9], obs_item[10], 0])
                    res_out = np.concatenate((front, right, behind, left), axis=None)
                else:
                    res_out = obs_item

                all_obs_list.append(res_out)
            all_obs_arr = np.array(all_obs_list)
            # print(all_obs_arr)
        #             print('ALL CORIDOR OBS ARR 1: ', all_obs_arr)
        #             print('ALL CORIDOR OBS ARR 1: ', all_obs_arr.shape)
        return all_obs_arr

    def reset(self):
        zeros_item = np.zeros([1, 2, 2])
        self.history_obstacles_list = list()
        for i in range(self.max_prev_obs):
            self.history_obstacles_list.append(zeros_item)

    def show(self, env):
        for laser_end_point in self.lasers_end_points:
            pygame.draw.line(env.gameDisplay, (200, 100, 100), self.host_object.position, laser_end_point)

        # for laser_collide in self.lasers_collides:
        #     pygame.draw.circle(env.gameDisplay, (0, 100, 64), laser_collide, 5)
        #for laser_collide in self.lasers_collides_item:
        #    pygame.draw.circle(env.gameDisplay, (200, 20, 64), laser_collide, 5)
        for i, lasers_collides_item in enumerate(self.lasers_collides_item_history):
            if i==len(self.lasers_collides_item_history)-1:
                for laser_collide in lasers_collides_item:
                    pygame.draw.circle(env.gameDisplay, (200, 20, 64), laser_collide, 5)
            else:
                for laser_collide in lasers_collides_item:
                    pygame.draw.circle(env.gameDisplay, (255, 75, 110), laser_collide, 3)

# TODO: Исправить, сенсор игнорирует все точки внутри корридора, препятствия тоже
class LeaderCorridor_Prev_lasers_v3(LeaderCorridor_Prev_lasers_v2):
    """
    It differs from LeaderCorridor_Prev_lasers_v2 in that it checks whether the points of intersection with the laser
    are located inside the corridor
    """
    def scan(self, env, corridor):
        raise ValueError("The sensor ignores all points inside the corridor, including obstacles, this is an error")
        self.lasers_collides = []
        self.lasers_end_points = []
        self.lasers_collides_item_history = []

        for i in range(self.lasers_count):
            self.lasers_end_points.append(self.host_object.position + rotateVector(np.array([self.laser_length, 0]),
                                                                                   (self.host_object.direction + self.first_laser_angle_offset) +
                                                                                   i * self.laser_period))
        # TODO: Исправить, не работает проверка на столкновение лазеров с препятствиями, если пустой коридор
        if len(corridor) > 1:
            corridor_lines = self.collect_obstacle_edges(env, corridor)
            # Сейчас в истории хранятся все грани препятствий, а не только пересеченные
            self.history_obstacles_list.pop(0)
            self.history_obstacles_list.append(corridor_lines)

            all_obs_list = []

            for j, corridor_lines_item in enumerate(self.history_obstacles_list):

                corridor_lines_item = np.array(corridor_lines_item)

                lasers_values_item = []
                self.lasers_collides_item = []
                for laser_end_point in self.lasers_end_points:
                    rez = LeaderCorridor_lasers.intersect(corridor_lines_item[:, 0, :], corridor_lines_item[:, 1, :],
                                                          np.array([self.host_object.position]),
                                                          np.array([laser_end_point]))
                    intersected_line_item = corridor_lines_item[rez]
                    if len(intersected_line_item) > 0:
                        x = LeaderCorridor_lasers.seg_intersect(intersected_line_item[:, 0, :],
                                                                intersected_line_item[:, 1, :],
                                                                np.array([self.host_object.position]),
                                                                np.array([laser_end_point]))
                        # TODO: исключить коллинеарные, вместо их точек пересечения добавить ближайшую точку коллинеарной границы
                        # но это бесполезно при использовании функции intersect, которая не работает с коллинеарными
                        exclude_rows = np.concatenate([np.nonzero(np.isinf(x))[0], np.nonzero(np.isnan(x))[0]])

                        if len(corridor) > 2:
                            # разбиваем корридор на 4ёхугольные сектора
                            for cor_segm_i in range(len(corridor) - 1):
                                rectangle_points = [corridor[cor_segm_i][0], corridor[cor_segm_i][1],
                                                    corridor[cor_segm_i + 1][0], corridor[cor_segm_i + 1][1]]
                                # так как прямоугольник может быть не выпуклый, пробуем превратить его в таковой,
                                # если это возможно (не всегда)
                                check1 = areDotsOnLeft(np.array([rectangle_points[0], rectangle_points[3]]),
                                                       np.array([rectangle_points[1], rectangle_points[2]]))
                                check2 = areDotsOnLeft(np.array([rectangle_points[1], rectangle_points[2]]),
                                                       np.array([rectangle_points[3], rectangle_points[0]]))
                                if ((check1 == [False, True]).all() and (check2 == [False, True]).all()):
                                    pass
                                elif ((check1 == [True, True]).all() and (check2 == [True, True]).all()):
                                    rectangle_points[1], rectangle_points[3] = rectangle_points[3], rectangle_points[1]
                                elif ((check1 == [False, False]).all() and (check2 == [False, False]).all()):
                                    rectangle_points[0], rectangle_points[2] = rectangle_points[2], rectangle_points[0]
                                elif ((check1 == [True, True]).all() and (check2 == [False, False]).all()):
                                    rectangle_points[0], rectangle_points[1] = rectangle_points[1], rectangle_points[0]
                                elif ((check1 == [False, False]).all() and (check2 == [True, True]).all()):
                                    rectangle_points[2], rectangle_points[3] = rectangle_points[3], rectangle_points[2]
                                elif ((check1 == [True, False]).all() and (check2 == [True, False]).all()):
                                    rectangle_points[2], rectangle_points[3] = rectangle_points[3], rectangle_points[2]
                                    rectangle_points[0], rectangle_points[1] = rectangle_points[1], rectangle_points[0]
                                elif (((check1 == [False, True]).all() or (check1 == [True, False]).all()) and (
                                        check2 == [True, True]).all()):
                                    pass  # warn("Впуклый прямоугольник, не обрабатывается корректно")
                                elif ((check1 == [True, True]).all() and (
                                        (check2 == [True, False]).all() or (check2 == [False, True]).all())):
                                    pass  # warn("Впуклый прямоугольник, не обрабатывается корректно")
                                elif ((check1 == [False, False]).all() and (
                                        (check2 == [True, False]).all() or (check2 == [False, True]).all())):
                                    pass  # warn("Впуклый прямоугольник, не обрабатывается корректно")
                                elif (((check1 == [False, True]).all() or (check1 == [True, False]).all()) and (
                                        check2 == [False, False]).all()):
                                    pass  # warn("Впуклый прямоугольник, не обрабатывается корректно")
                                else:
                                    raise ValueError(
                                        "Не предвидел такой вариант расположения вершин прямоугольника (сегмента "
                                        "корридора) при проверке, находятся ли точки внутри него: check1:{}, "
                                        "check2:{}".format(str(check1), str(check2)))
                                # проверяем точки пересечений луча и стенок на то, находятся ли они внутри этого прямоугольника или нет.
                                # Проверка для каждой стороны 4-ёхугольника, лежат ли точки слева от неё. Точки, которые слева от всех сторон - внутри многоугольника.
                                # TODO: Не работает с впуклыми многоугольниками, возможно стоит попробовать алгоритм с лучами или ещё что-то.
                                line = np.array([rectangle_points[0], rectangle_points[1]])
                                insideDots_currRectangle = areDotsOnLeft(line, x)
                                line = np.array([rectangle_points[1], rectangle_points[3]])
                                insideDots_currRectangle &= areDotsOnLeft(line, x)
                                line = np.array([rectangle_points[3], rectangle_points[2]])
                                insideDots_currRectangle &= areDotsOnLeft(line, x)
                                line = np.array([rectangle_points[2], rectangle_points[0]])
                                insideDots_currRectangle &= areDotsOnLeft(line, x)
                                if cor_segm_i == 0:
                                    dotsInsideCorridor = insideDots_currRectangle
                                else:
                                    dotsInsideCorridor |= insideDots_currRectangle
                        x = x[~dotsInsideCorridor]
                        if len(x) > 0:
                            norms = np.linalg.norm(x - self.host_object.position, axis=1)
                            lasers_values_item.append(np.min(norms))
                            closest_dot_idx = np.argmin(np.linalg.norm(x - self.host_object.position, axis=1))
                            self.lasers_collides_item.append(x[closest_dot_idx])
                        else:
                            self.lasers_collides_item.append(laser_end_point)
                    else:
                        self.lasers_collides_item.append(laser_end_point)
                self.lasers_collides_item_history.append(self.lasers_collides_item.copy())

                obs_item = np.ones(self.lasers_count, dtype=np.float32) * self.laser_length
                for i, collide in enumerate(self.lasers_collides_item):
                    obs_item[i] = np.linalg.norm(collide - self.host_object.position)

                if self.pad_sectors:
                    front = np.zeros(len(obs_item))
                    right = np.zeros(len(obs_item))
                    behind = np.zeros(len(obs_item))
                    left = np.zeros(len(obs_item))

                    lasers_in_sector = self.lasers_count / 4
                    for i in range(len(obs_item)):
                        if i < lasers_in_sector:
                            front[i] = obs_item[i]
                        elif lasers_in_sector <= i < 2 * lasers_in_sector:
                            right[i] = obs_item[i]
                        elif 2 * lasers_in_sector <= i < 3 * lasers_in_sector:
                            behind[i] = obs_item[i]
                        else:
                            left[i] = obs_item[i]

                    # front = np.array([obs_item[0], obs_item[1], 0, 0, 0, 0, 0, 0, 0, 0, 0, obs_item[11]])
                    # right = np.array([0, 0, obs_item[2], obs_item[3], obs_item[4], 0, 0, 0, 0, 0, 0, 0])
                    # behind = np.array([0, 0, 0, 0, 0, obs_item[5], obs_item[6], obs_item[7], 0, 0, 0, 0])
                    # left = np.array([0, 0, 0, 0, 0, 0, 0, 0, obs_item[8], obs_item[9], obs_item[10], 0])
                    res_out = np.concatenate((front, right, behind, left), axis=None)
                else:
                    res_out = obs_item

                all_obs_list.append(res_out)
            all_obs_arr = np.array(all_obs_list)
            # print(all_obs_arr)
        #             print('ALL CORIDOR OBS ARR 1: ', all_obs_arr)
        #             print('ALL CORIDOR OBS ARR 1: ', all_obs_arr.shape)
        return all_obs_arr


#TODO: класс наследует некоторые атрибуты, которые ему не нужны, возможно получится сделать это как-то чище,
# без наследования
class LeaderCorridor_lasers_compas(LeaderCorridor_Prev_lasers_v2):
    """
    The same sensor with beams, but has an additional output indicating which of the lasers rest on the front/back/left/right walls of the corridor.
    Reacts only to the corridor
    """
    def __init__(self, *args, **kwargs):
        super(LeaderCorridor_lasers_compas, self).__init__(*args, **kwargs)
        self.lasers_collides_item_history = []
        self.lasers_collides_corridor_orientation_history = []
        self.lasers_collides_corridor_orientation = []
        if not self.react_to_safe_corridor or not self.react_to_green_zone or self.react_to_obstacles:
            raise ValueError("Unsupported set of flags for LeaderCorridor_lasers_compas class, now implemented"
                             "only option for flags: "
                             "react_to_safe_corridor=True, react_to_green_zone=True, react_to_obstacles=False")

    def collect_obstacle_edges(self, env, corridor):
        left_walls, right_walls = [], []
        if self.react_to_safe_corridor:
            for i in range(len(corridor) - 1):
                right_walls.append([corridor[i][0], corridor[i + 1][0]])
                left_walls.append([corridor[i][1], corridor[i + 1][1]])
        left_walls = np.array(left_walls)
        right_walls = np.array(right_walls)

        front_wall, back_wall = None, None
        if self.react_to_green_zone:
            back_wall = np.array([corridor[0][0], corridor[0][1]])
            front_wall = np.array([corridor[-1][0], corridor[-1][1]])
        corridor_lines = np.concatenate([[front_wall], [back_wall], left_walls, right_walls], axis=0)
        # walls orientation - каждой грани соответствует 1 в одном из 4 столбцов:
        # 1 - передняя стена, 2 - задняя, 3 - левые стены, 4 - правые
        walls_orientations = np.zeros((len(corridor_lines), 4))
        walls_orientations[0, 0] = 1
        walls_orientations[1, 1] = 1
        walls_orientations[2:2+len(left_walls), 2] = 1
        walls_orientations[2 + len(left_walls):, 3] = 1
        return corridor_lines, walls_orientations

    def scan(self, env, corridor):
        self.lasers_collides = []
        self.lasers_end_points = []
        self.lasers_collides_item_history = []
        self.lasers_collides_corridor_orientation_history = []

        for i in range(self.lasers_count):
            self.lasers_end_points.append(self.host_object.position + rotateVector(np.array([self.laser_length, 0]),
                                                                                   (self.host_object.direction + self.first_laser_angle_offset) +
                                                                                   i * self.laser_period))
        # TODO: Исправить, не работает проверка на столкновение лазеров с препятствиями, если пустой коридор
        if len(corridor) > 1:
            corridor_lines, walls_orientations = self.collect_obstacle_edges(env, corridor)
            # Сейчас в истории хранятся все грани препятствий, а не только пересеченные
            self.history_obstacles_list.pop(0)
            self.history_obstacles_list.append((corridor_lines, walls_orientations))
            all_obs_list = []
            for j, corridor_lines_item in enumerate(self.history_obstacles_list):
                corridor_lines, walls_orientations = corridor_lines_item
                lasers_values_item = []
                self.lasers_collides_item = []
                self.lasers_collides_corridor_orientation = []
                for laser_end_point in self.lasers_end_points:

                    rez = LeaderCorridor_lasers.intersect(corridor_lines[:, 0, :], corridor_lines[:, 1, :],
                                                          np.array([self.host_object.position]),
                                                          np.array([laser_end_point]))
                    intersected_line_item = corridor_lines[rez]
                    chosen_walls_orientations = walls_orientations[rez]
                    if len(intersected_line_item) > 0:
                        x = LeaderCorridor_lasers.seg_intersect(intersected_line_item[:, 0, :],
                                                                intersected_line_item[:, 1, :],
                                                                np.array([self.host_object.position]),
                                                                np.array([laser_end_point]))
                        # TODO: исключить коллинеарные, вместо их точек пересечения добавить ближайшую точку коллинеарной границы
                        # но это бесполезно при использовании функции intersect, которая не работает с коллинеарными
                        exclude_rows = np.concatenate([np.nonzero(np.isinf(x))[0], np.nonzero(np.isnan(x))[0]])
                        norms = np.linalg.norm(x - self.host_object.position, axis=1)
                        lasers_values_item.append(np.min(norms))
                        closest_dot_idx = np.argmin(np.linalg.norm(x - self.host_object.position, axis=1))
                        self.lasers_collides_item.append(x[closest_dot_idx])
                        self.lasers_collides_corridor_orientation.append(chosen_walls_orientations[closest_dot_idx])
                    else:
                        self.lasers_collides_item.append(laser_end_point)
                        self.lasers_collides_corridor_orientation.append(np.zeros(4))
                self.lasers_collides_item_history.append(self.lasers_collides_item.copy())
                self.lasers_collides_corridor_orientation_history.append(self.lasers_collides_corridor_orientation.copy())

                obs_item = np.zeros(self.lasers_count*5, dtype=np.float32)
                obs_item[:self.lasers_count] = 1
                for i, (collide, orientation) in enumerate(zip(self.lasers_collides_item, self.lasers_collides_corridor_orientation)):
                    if (orientation == 0).all():
                        obs_item[i] = np.linalg.norm(collide - self.host_object.position)
                    elif orientation[0] == 1:
                        obs_item[i] = 0
                        obs_item[i + self.lasers_count*1] = np.linalg.norm(collide - self.host_object.position)
                    elif orientation[1] == 1:
                        obs_item[i] = 0
                        obs_item[i + self.lasers_count*2] = np.linalg.norm(collide - self.host_object.position)
                    elif orientation[2] == 1:
                        obs_item[i] = 0
                        obs_item[i + self.lasers_count * 3] = np.linalg.norm(
                            collide - self.host_object.position)
                    elif orientation[3] == 1:
                        obs_item[i] = 0
                        obs_item[i + self.lasers_count * 4] = np.linalg.norm(
                            collide - self.host_object.position)
                all_obs_list.append(obs_item)
            all_obs_arr = np.array(all_obs_list)
        #             print('ALL CORIDOR OBS ARR 1: ', all_obs_arr)
        #             print('ALL CORIDOR OBS ARR 1: ', all_obs_arr.shape)
        #print(all_obs_arr)
        return all_obs_arr

    def reset(self):
        zeros_item = np.zeros([1, 2, 2])
        self.history_obstacles_list = list()
        for i in range(self.max_prev_obs):
            self.history_obstacles_list.append((zeros_item, np.zeros((1, 4))))

    def show(self, env):
        for laser_end_point in self.lasers_end_points:
            pygame.draw.line(env.gameDisplay, (200, 100, 100), self.host_object.position, laser_end_point)

        # for laser_collide in self.lasers_collides:
        #     pygame.draw.circle(env.gameDisplay, (0, 100, 64), laser_collide, 5)
        #for laser_collide in self.lasers_collides_item:
        #    pygame.draw.circle(env.gameDisplay, (200, 20, 64), laser_collide, 5)
        for i, (lasers_collides_item, orientation) in enumerate(zip(self.lasers_collides_item_history, self.lasers_collides_corridor_orientation_history)):
            if i == len(self.lasers_collides_item_history)-1:
                for laser_collide, laser_orientation in zip(lasers_collides_item, orientation):
                    if (laser_orientation == 0).all():
                        pygame.draw.circle(env.gameDisplay, (20, 20, 20), laser_collide, 5)
                    elif laser_orientation[0] == 1:
                        pygame.draw.circle(env.gameDisplay, (200, 20, 200), laser_collide, 5)
                    elif laser_orientation[1] == 1:
                        pygame.draw.circle(env.gameDisplay, (200, 200, 20), laser_collide, 5)
                    elif laser_orientation[2] == 1:
                        pygame.draw.circle(env.gameDisplay, (200, 20, 20), laser_collide, 5)
                    elif laser_orientation[3] == 1:
                        pygame.draw.circle(env.gameDisplay, (20, 200, 20), laser_collide, 5)
            else:
                for laser_collide, laser_orientation in zip(lasers_collides_item, orientation):
                    if (laser_orientation == 0).all():
                        pygame.draw.circle(env.gameDisplay, (60, 60, 60), laser_collide, 3)
                    elif laser_orientation[0] == 1:
                        pygame.draw.circle(env.gameDisplay, (255, 60, 255), laser_collide, 3)
                    elif laser_orientation[1] == 1:
                        pygame.draw.circle(env.gameDisplay, (255, 255, 60), laser_collide, 3)
                    elif laser_orientation[2] == 1:
                        pygame.draw.circle(env.gameDisplay, (255, 60, 60), laser_collide, 3)
                    elif laser_orientation[3] == 1:
                        pygame.draw.circle(env.gameDisplay, (60, 255, 60), laser_collide, 3)

# Можно конечно через getattr из модуля брать, но так можно проверку добавить
SENSOR_CLASSNAME_TO_CLASS = {
    "LaserSensor": LaserSensor,
    "LeaderPositionsTracker": LeaderPositionsTracker,
    "LeaderPositionsTracker_v2": LeaderPositionsTracker_v2,
    "LeaderTrackDetector_vector": LeaderTrackDetector_vector,
    "LeaderTrackDetector_radar": LeaderTrackDetector_radar,
    "LeaderCorridor_lasers": LeaderCorridor_lasers,
    "GreenBoxBorderSensor": GreenBoxBorderSensor,
    "LeaderCorridor_lasers_v2": LeaderCorridor_lasers_v2,
    "LeaderObstacles_lasers": LeaderObstacles_lasers,
    "Leader_Dyn_Obstacles_lasers": Leader_Dyn_Obstacles_lasers,
    "FollowerInfo": FollowerInfo,
    "LaserPrevSensor": LaserPrevSensor,
    "LeaderCorridor_Prev_lasers_v2": LeaderCorridor_Prev_lasers_v2,
    "LeaderCorridor_Prev_lasers_v3": LeaderCorridor_Prev_lasers_v3,
    "LeaderCorridor_lasers_compas": LeaderCorridor_lasers_compas,
}
