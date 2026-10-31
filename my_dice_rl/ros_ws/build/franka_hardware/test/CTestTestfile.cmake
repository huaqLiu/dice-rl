# CMake generated Testfile for 
# Source directory: /home/lhq/dice-rl/my_dice_rl/ros_ws/src/franka_ros2-0.1.0/franka_hardware/test
# Build directory: /home/lhq/dice-rl/my_dice_rl/ros_ws/build/franka_hardware/test
# 
# This file includes the relevant testing commands required for 
# testing this directory and lists subdirectories to be tested as well.
add_test(franka_hardware_test "/usr/bin/python3" "-u" "/opt/ros/humble/share/ament_cmake_test/cmake/run_test.py" "/home/lhq/dice-rl/my_dice_rl/ros_ws/build/franka_hardware/test_results/franka_hardware/franka_hardware_test.gtest.xml" "--package-name" "franka_hardware" "--output-file" "/home/lhq/dice-rl/my_dice_rl/ros_ws/build/franka_hardware/ament_cmake_gmock/franka_hardware_test.txt" "--command" "/home/lhq/dice-rl/my_dice_rl/ros_ws/build/franka_hardware/test/franka_hardware_test" "--gtest_output=xml:/home/lhq/dice-rl/my_dice_rl/ros_ws/build/franka_hardware/test_results/franka_hardware/franka_hardware_test.gtest.xml")
set_tests_properties(franka_hardware_test PROPERTIES  LABELS "gmock" REQUIRED_FILES "/home/lhq/dice-rl/my_dice_rl/ros_ws/build/franka_hardware/test/franka_hardware_test" TIMEOUT "60" WORKING_DIRECTORY "/home/lhq/dice-rl/my_dice_rl/ros_ws/build/franka_hardware/test" _BACKTRACE_TRIPLES "/opt/ros/humble/share/ament_cmake_test/cmake/ament_add_test.cmake;125;add_test;/opt/ros/humble/share/ament_cmake_gmock/cmake/ament_add_gmock.cmake;106;ament_add_test;/opt/ros/humble/share/ament_cmake_gmock/cmake/ament_add_gmock.cmake;52;_ament_add_gmock;/home/lhq/dice-rl/my_dice_rl/ros_ws/src/franka_ros2-0.1.0/franka_hardware/test/CMakeLists.txt;3;ament_add_gmock;/home/lhq/dice-rl/my_dice_rl/ros_ws/src/franka_ros2-0.1.0/franka_hardware/test/CMakeLists.txt;0;")
subdirs("../gmock")
subdirs("../gtest")
