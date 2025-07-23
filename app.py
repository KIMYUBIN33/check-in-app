import os
from datetime import datetime, date, timedelta, timezone
from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy

# --- 1. 앱 생성 및 DB 설정 ---
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(os.path.abspath(os.path.dirname(__file__)), 'study.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- 2. 데이터 모델(테이블) 정의 ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    time_debt = db.Column(db.Integer, default=0)
    last_updated = db.Column(db.Date, default=date.today)

class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(20), default='studying', nullable=False)
    total_study_seconds = db.Column(db.Integer, default=0)
    check_in_time = db.Column(db.DateTime, nullable=False) # 출석 시간 기록
    last_event_time = db.Column(db.DateTime, nullable=False)
    check_in_date = db.Column(db.Date, nullable=False)
    check_out_time = db.Column(db.DateTime, nullable=True)
    penalty = db.Column(db.Integer, default=0) # 벌금 기록
    user = db.relationship('User', backref=db.backref('attendances', lazy=True))

# --- 앱 컨텍스트 안에서 DB 테이블 생성 ---
with app.app_context():
    db.create_all()

# --- 3. 헬퍼 함수 ---
def get_kst_today():
    # UTC 시간대를 명시적으로 지정
    utc_now = datetime.now(timezone.utc)
    # KST (UTC+9)로 변환
    kst_now = utc_now + timedelta(hours=9)
    return kst_now.date()

# --- 4. 라우트(경로) 및 기능 함수 ---
@app.route('/')
def index():
    today = get_kst_today()
    users = User.query.all()

    # --- 시간 정산 로직 (개선된 버전) ---
    for user in users:
        if user.last_updated < today:
            days_to_process = (today - user.last_updated).days
            
            # 매주 월요일에 부채 리셋 (기존 로직 유지)
            # isocalendar() -> (year, week, weekday)
            if today.weekday() == 0 and user.last_updated.isocalendar()[1] < today.isocalocalendar()[1]:
                user.time_debt = 0

            for i in range(days_to_process):
                day_to_check = user.last_updated + timedelta(days=i + 1)
                
                # 주말(토,일)은 건너뜀
                if day_to_check.weekday() >= 5:
                    continue

                studied_seconds = 0
                # 해당 날짜의 모든 로그를 찾음 (퇴장 여부와 상관없이)
                log = Attendance.query.filter_by(user_id=user.id, check_in_date=day_to_check).first()

                if log:
                    # Case 1: 로그는 있으나 퇴장 처리를 잊은 경우
                    if not log.check_out_time:
                        log.check_out_time = log.last_event_time # 마지막 활동 시간을 퇴장 시간으로 간주
                        log.status = 'completed'
                        # 공부 시간은 0으로 처리하여 페널티 부과
                        studied_seconds = 0
                    # Case 2: 정상적으로 퇴장 처리된 경우
                    else:
                        studied_seconds = log.total_study_seconds
                # Case 3: 로그가 아예 없는 경우 (결석)
                else:
                    studied_seconds = 0
                
                daily_target = 14400 # 4시간
                user.time_debt += (daily_target - studied_seconds)

            user.last_updated = today
    db.session.commit()


    # --- HTML에 전달할 데이터 준비 ---
    today_logs = Attendance.query.filter(Attendance.check_in_date == today).all()
    today_stats = {}
    for log in today_logs:
        # 퇴장했으면 'completed' 상태로 고정
        status = 'completed' if log.check_out_time else log.status
        today_stats[log.user_id] = {
            'status': status,
            'total_seconds': log.total_study_seconds,
            'last_event_timestamp': log.last_event_time.timestamp() if status == 'studying' else 0,
            # 수동 처리를 위해 현재 로그의 ID와 미완료 상태를 전달
            'log_id': log.id,
            'is_unclosed': not log.check_out_time
        }
    
    all_records = Attendance.query.order_by(Attendance.check_in_time.desc()).all()

    return render_template('index.html', users=users, today_stats=today_stats, all_records=all_records)

@app.route('/handle_attendance', methods=['POST'])
def handle_attendance():
    username = request.form['username'].strip()
    action = request.form['action']

    if not username:
        return redirect(url_for('index'))

    user = User.query.filter_by(username=username).first()
    if not user:
        user = User(username=username, last_updated=get_kst_today())
        db.session.add(user)
        db.session.commit()

    today_kst = get_kst_today()
    active_log = Attendance.query.filter_by(user_id=user.id, check_out_time=None).first()

    if action == '출석':
        todays_log = Attendance.query.filter_by(user_id=user.id, check_in_date=today_kst).first()

        # 오늘 출석 기록이 없을 경우에만 새로 생성
        if not todays_log:
            now_utc = datetime.now(timezone.utc)

            # TODO: 지각 여부를 판단하고 벌금을 계산하는 로직 필요
            # 예시: kst_now = now_utc + timedelta(hours=9)
            #      is_late = kst_now.hour >= 13
            #      calculated_penalty = 1000 if is_late else 0
            calculated_penalty = 0 # 임시로 0으로 설정

            new_log = Attendance(
                user_id=user.id,
                status='studying',
                check_in_time=now_utc,
                last_event_time=now_utc,
                check_in_date=today_kst,
                penalty=calculated_penalty
            )
            db.session.add(new_log)

    elif action == '중단' and active_log and active_log.status == 'studying':
        now_utc = datetime.now(timezone.utc) # utcnow()로 통일
        aware_last_event_time = active_log.last_event_time.replace(tzinfo=timezone.utc)
        duration = (now_utc - aware_last_event_time).total_seconds()
        active_log.total_study_seconds += int(duration)
        active_log.status = 'paused'
        active_log.last_event_time = now_utc

    elif action == '재개' and active_log and active_log.status == 'paused':
        active_log.status = 'studying'
        active_log.last_event_time = datetime.now(timezone.utc) # utcnow()로 통일

    elif action == '퇴장' and active_log:
        now_utc = datetime.now(timezone.utc) # utcnow()로 통일
        if active_log.status == 'studying':
            aware_last_event_time = active_log.last_event_time.replace(tzinfo=timezone.utc)
            duration = (now_utc - aware_last_event_time).total_seconds()
            active_log.total_study_seconds += int(duration)
        
        active_log.check_out_time = now_utc
        daily_target = 14400 if today_kst.weekday() < 5 else 0
        user.time_debt += (daily_target - active_log.total_study_seconds)

    db.session.commit()
    return redirect(url_for('index'))

@app.route('/force_checkout/<int:log_id>', methods=['POST'])
def force_checkout(log_id):
    # or_404: id에 해당하는 로그가 없으면 404 에러 발생
    log = db.get_or_404(Attendance, log_id)

    # 안전장치: 이미 퇴장 처리된 로그는 건드리지 않음
    if log and not log.check_out_time:
        now_utc = datetime.now(timezone.utc)
        
        # 만약 강제 퇴장 시점에도 공부 중이었다면, 마지막 시간까지 계산
        if log.status == 'studying':
            aware_last_event_time = log.last_event_time.replace(tzinfo=timezone.utc)
            duration = (now_utc - aware_last_event_time).total_seconds()
            log.total_study_seconds += int(duration)

        log.check_out_time = now_utc
        log.status = 'completed' # 상태를 '완료'로 변경
        
        # 당일 퇴장에 대한 부채 정산
        today_kst = get_kst_today()
        user = User.query.get(log.user_id)
        if user and log.check_in_date == today_kst:
            daily_target = 14400 if today_kst.weekday() < 5 else 0
            user.time_debt += (daily_target - log.total_study_seconds)

        db.session.commit()

    return redirect(url_for('index'))

# --- 5. 서버 실행 ---
if __name__ == '__main__':
    app.run(debug=True)
