#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
g++ -std=c++17 -Wall -Wextra -Werror -Icore tests/safety_test.cpp -o /tmp/irrigation-safety-test
/tmp/irrigation-safety-test
g++ -std=c++17 -Wall -Wextra -Werror -Icore tests/shared_rig_test.cpp -o /tmp/irrigation-shared-rig-test
/tmp/irrigation-shared-rig-test
g++ -std=c++17 -Wall -Wextra -Werror -Icore tests/sensor_math_test.cpp -o /tmp/irrigation-sensor-math-test
/tmp/irrigation-sensor-math-test
fresh_config=$(mktemp /tmp/irrigation-sdkconfig.XXXXXX)
idf.py -D SDKCONFIG="$fresh_config" reconfigure
idf.py build
# Separate synthetic configuration, never change the default workspace configuration.
simulation_dir=$(mktemp -d /tmp/irrigation-simulation.XXXXXX)
cp CMakeLists.txt sdkconfig.defaults partitions.csv "$simulation_dir/"
cp -r main core "$simulation_dir/"
printf '\nCONFIG_IRRIGATION_SIMULATION=y\n' >> "$simulation_dir/sdkconfig.defaults"
cd "$simulation_dir"
idf.py build
