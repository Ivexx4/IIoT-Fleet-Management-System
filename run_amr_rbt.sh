pkill -f "python3 amr_robot.py" || true

sleep 1
python3 amr_robot.py 2024209219 RBT1 &
python3 amr_robot.py 2024209219 RBT2 &
python3 amr_robot.py 2024209219 RBT3 &
python3 amr_robot.py 2024209219 RBT4 &
