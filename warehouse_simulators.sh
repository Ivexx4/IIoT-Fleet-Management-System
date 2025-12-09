pkill -f "python3 amr_robot.py" || true
pkill -f "python3 shelves.py" || true
sleep 1
python3 amr_robot.py 2024209219 RBT1 &
python3 amr_robot.py 2024209219 RBT2 &
python3 amr_robot.py 2024209219 RBT3 &
python3 amr_robot.py 2024209219 RBT4 &
python3 shelves.py 2024209219 storage-a S1 60 &
python3 shelves.py 2024209219 storage-a S2 60 &
python3 shelves.py 2024209219 storage-a S3 60 &
python3 shelves.py 2024209219 storage-a S4 60 &
python3 shelves.py 2024209219 storage-a S5 60 &
python3 shelves.py 2024209219 storage-b S6 60 &
python3 shelves.py 2024209219 storage-b S7 60 &
python3 shelves.py 2024209219 storage-b S8 60 &
python3 shelves.py 2024209219 storage-b S9 60 &
python3 shelves.py 2024209219 storage-b S10 60 &
