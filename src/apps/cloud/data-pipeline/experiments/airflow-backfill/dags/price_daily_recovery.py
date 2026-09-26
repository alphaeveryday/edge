"""C: 업무일별 가격 정제·적재·확인 DAG — 업무 코드는 A·B 와 같은 lab.py step 으로 부른다.

업무일은 logical_date(15:40 KST 슬롯)에서 KST 날짜로 명시 전달하고, 입력(run_id)은
select_input 이 고정 입력 목록에서 고른다. 재시도 0·동시 실행 1 은 A·B 와 같다.
"""

import pendulum
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG
from airflow.timetables.trigger import CronTriggerTimetable

LAB = "/opt/dp/bin/python /lab/lab.py"
RID = "{{ ti.xcom_pull(task_ids='select_input') }}"

with DAG(
    dag_id="price_daily_recovery",
    schedule=CronTriggerTimetable("40 15 * * 1-5", timezone="Asia/Seoul"),
    start_date=pendulum.datetime(2026, 9, 1, tz="Asia/Seoul"),
    # 실험 입력 범위 밖 정기 실행이 생기지 않게 막는다(unpause 해도 새 스케줄 실행 없음)
    end_date=pendulum.datetime(2026, 9, 24, tz="Asia/Seoul"),
    catchup=False,
    max_active_runs=1,
    max_active_tasks=1,
    default_args={"retries": 0},
):
    select_input = BashOperator(
        task_id="select_input",
        bash_command=f"{LAB} select-input {{{{ logical_date.in_timezone('Asia/Seoul').to_date_string() }}}}",
    )
    normalize = BashOperator(
        task_id="normalize_price",
        bash_command=f"{LAB} step normalize-price --run-id {RID} --input-run-id {RID}",
    )
    load = BashOperator(
        task_id="load_price_daily",
        bash_command=f"{LAB} step load-price-daily --run-id {RID} --input-run-id {RID}",
    )
    check = BashOperator(task_id="check_run", bash_command=f"{LAB} check-run {RID}")
    select_input >> normalize >> load >> check
