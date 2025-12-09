GROUP_ID="2024209219"
PYTHON_CMD="python3"
pkill -f "amr_robot.py"
pkill -f "shelves.py"
pkill -f "loading_station.py"
sleep 1
$PYTHON_CMD shelves.py 2024209219 storage-a S1 2 &
sleep 0.1
$PYTHON_CMD shelves.py 2024209219 storage-a S2 2 &
sleep 0.1
$PYTHON_CMD shelves.py 2024209219 storage-a S3 2 &
sleep 0.1
$PYTHON_CMD shelves.py 2024209219 storage-a S4 2 &
sleep 0.1
$PYTHON_CMD shelves.py 2024209219 storage-a S5 2 &
sleep 0.1
$PYTHON_CMD shelves.py 2024209219 storage-b S6 2 &
sleep 0.1
$PYTHON_CMD shelves.py 2024209219 storage-b S7 2 &
sleep 0.1
$PYTHON_CMD shelves.py 2024209219 storage-b S8 2 &
sleep 0.1
$PYTHON_CMD shelves.py 2024209219 storage-b S9 2 &
sleep 0.1
$PYTHON_CMD shelves.py 2024209219 storage-b S10 2 &
sleep 0.1
$PYTHON_CMD loading_station.py 2024209219 P1 &
sleep 0.1
$PYTHON_CMD loading_station.py 2024209219 P2 &
sleep 0.1
$PYTHON_CMD amr_robot.py 2024209219 RBT1 &
sleep 0.1
$PYTHON_CMD amr_robot.py 2024209219 RBT2 &
sleep 0.1
$PYTHON_CMD amr_robot.py 2024209219 RBT3 &
sleep 0.1
$PYTHON_CMD amr_robot.py 2024209219 RBT4 &
sleep 0.1