pkill -f "python3 shelves.py" || true
sleep 1
python3 shelves.py 2024209219 storage-a S1 60 &
sleep 0.5
python3 shelves.py 2024209219 storage-a S2 60 &
sleep 0.5
python3 shelves.py 2024209219 storage-a S3 60 &
sleep 0.5
python3 shelves.py 2024209219 storage-a S4 60 &
sleep 0.5
python3 shelves.py 2024209219 storage-a S5 60 &
sleep 0.5
python3 shelves.py 2024209219 storage-b S6 60 &
sleep 0.5
python3 shelves.py 2024209219 storage-b S7 60 &
sleep 0.5
python3 shelves.py 2024209219 storage-b S8 60 &
sleep 0.5
python3 shelves.py 2024209219 storage-b S9 60 &
sleep 0.5
python3 shelves.py 2024209219 storage-b S10 60 &

