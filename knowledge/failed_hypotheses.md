
## [2026-06-27 15:27:22] H001 激活组合因子 north_plus_limit_up：北向大幅流入+涨停板密集→强势市场信号叠加
- **层次**: L3 / logic_upgrade
- **改动**: {"combo_factor": "north_plus_limit_up", "action": "activate", "condition": "north_money>=50亿 AND limit_up_count>=30", "extra_weight": 15}
- **失败原因**: 实验未通过: 组合因子触发样本不足或未优于基准
- **优先级**: 0.5

## [2026-06-27 15:27:22] H002 激活组合因子 north_out_plus_margin_down：北向大幅流出+融资余额下降→弱势市场信号叠加
- **层次**: L3 / logic_upgrade
- **改动**: {"combo_factor": "north_out_plus_margin_down", "action": "activate", "condition": "north_money<=-50亿 AND margin_balance_decreasing", "extra_weight": 10}
- **失败原因**: 实验未通过: 组合因子触发样本不足或未优于基准
- **优先级**: 0.5

## [2026-06-27 15:27:22] H001 激活组合因子 north_plus_limit_up：北向大幅流入+涨停板密集→强势市场信号叠加
- **层次**: L3 / logic_upgrade
- **改动**: {"combo_factor": "north_plus_limit_up", "action": "activate", "condition": "north_money>=50亿 AND limit_up_count>=30", "extra_weight": 15}
- **失败原因**: 实验未通过: 组合因子触发样本不足或未优于基准
- **优先级**: 0.5

## [2026-06-27 15:27:22] H002 激活组合因子 north_out_plus_margin_down：北向大幅流出+融资余额下降→弱势市场信号叠加
- **层次**: L3 / logic_upgrade
- **改动**: {"combo_factor": "north_out_plus_margin_down", "action": "activate", "condition": "north_money<=-50亿 AND margin_balance_decreasing", "extra_weight": 10}
- **失败原因**: 实验未通过: 组合因子触发样本不足或未优于基准
- **优先级**: 0.5
