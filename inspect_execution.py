"""Read-only execution occupancy CLI; importing this module starts no service."""
import argparse
import json
import sys

from execution_inspection import InspectionQueryError, read_occupancy_snapshot


CLASSIFICATIONS = {
    'record_inconsistent': '占用记录不一致 / 残留认领',
    'current_instance_occupied': '当前实例占用（不代表进程存活）',
    'previous_instance_retained': '旧实例遗留占用',
    'instance_relation_unknown': '存在记录占用；当前实例关系未知',
    'session_needs_validation': '会话待校验（不等于活动占用）',
    'no_recorded_occupancy': '未发现记录占用（不证明进程退出）',
}


def _display(value):
    # Escape control characters in stored IDs before terminal output.
    return '未记录' if value is None else json.dumps(value, ensure_ascii=False)


def render_text(entries):
    lines = ['6A 只读检查：退出情况未知；不提供解除操作。',
             'observed_at 仅为查询时刻；执行阶段时间未记录。']
    if not entries:
        lines.append('当前查询范围未发现相关记录；这不是进程退出证明。')
    for entry in entries:
        domain = entry['domain'] or {}
        session = entry['session'] or {}
        lines.extend([
            '', f"对象：{_display(entry['agent_id'])} / {_display(entry['conversation_id'])}",
            '分类：' + CLASSIFICATIONS[entry['classification']],
            f"原因：{entry['reason_code']}；记录阻断：{entry['blocked']}",
            f"实例关系：{entry['owner_relation']}；查询时刻：{entry['observed_at']}",
            f"域：{_display(domain.get('state'))}；owner={_display(domain.get('owner_run_id'))}；fence={_display(domain.get('fence'))}",
            f"session：{_display(session.get('state'))}；ID={_display(session.get('session_id'))}；owner={_display(session.get('owner_run_id'))}；generation={_display(session.get('generation'))}",
            f"历史候选数量：{session.get('candidate_count', 0)}；退出状态：未知",
        ])
        for run in entry['runs']:
            lines.append(
                f"run：{_display(run['run_id'])}；最后记录阶段={_display(run['state'])}；"
                f"job={_display(run['source_job_id'])}；call={_display(run['source_call_id'])}；"
                f"message={_display(run['source_message_id'])}；取消意图={run['cancel_requested']}；"
                f"exit_confirmed原记录={run['exit_confirmed']}（非实时证明）")
        for binding in entry['legacy_bindings']:
            lines.append(f"旧绑定：{binding['source_table']}；ID={_display(binding['session_id'])}；范围未知")
        for issue in entry['issues']:
            lines.append(f"异常：{issue['reason_code']}；run={_display(issue['run_id'])}")
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description='6A 遗留占用只读检查（不释放、不恢复）')
    parser.add_argument('--db', required=True, help='现有数据库路径；缺失时不创建')
    parser.add_argument('--agent', help='按 agent_id 过滤')
    parser.add_argument('--conversation', help='按 conversation_id 过滤')
    parser.add_argument('--json', action='store_true', help='输出结构化快照')
    args = parser.parse_args(argv)
    try:
        entries = read_occupancy_snapshot(args.db, agent_id=args.agent,
                                          conversation_id=args.conversation)
    except InspectionQueryError:
        # No raw exception/candidate/config data, and no success-looking stdout.
        print('[查询失败] 无法读取占用快照；请核对数据库路径、版本、访问权限及迁移状态后重试。',
              file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(dict(exit_status='unknown', instance_relation='unknown',
                              entries=entries), ensure_ascii=False, indent=2))
    else:
        print(render_text(entries))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
