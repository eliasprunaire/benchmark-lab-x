"""Current model selection policy, separate from immutable historical receipts"""

DEEPSEEK_REPLACEMENT = 'deepseek/deepseek-v4.1-flash'
RETIREMENT_NOTICE = ('DeepSeek V4 Flash 0731 est retiré des nouveaux essais et remplacé par '
                     'DeepSeek V4.1 Flash. Les résultats historiques restent consultables.')


def require_current(configuration):
    for field in ('model', 'revision'):
        model = configuration.get(field)
        if isinstance(model, str) and model.lower().removeprefix('deepseek/') in (
                'deepseek-v4-flash-0731', 'deepseek-v4-flash'):
            raise ValueError(RETIREMENT_NOTICE + ' Sélectionnez ' + DEEPSEEK_REPLACEMENT + '.')
