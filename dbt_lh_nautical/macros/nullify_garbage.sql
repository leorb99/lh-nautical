{% macro nullify_garbage(column_name) %}
    -- Comparison is case-insensitive, but the returned value preserves the
    -- original casing (only trimmed). Do not lowercase the else branch: it
    -- previously forced every non-garbage value permanently to lowercase,
    -- which has nothing to do with detecting placeholder/garbage text.
    -- The comparison list must stay all-lowercase: the left side is always
    -- lower(column_name), so a mixed-case entry here can never match.
    -- 'TODO: add description' previously had its original casing and was
    -- silently dead for that reason.
    case
        when trim(lower({{ column_name }})) in (
            'asdf', '???', '??', '?', 'fixme', 'n/a', 'null', 'test', 'todo', 'xxx', '.', 'tbd', '--', '-', 'todo: add description',
            'cliente genérico', 'genérico', 'joão da silva', 'nao informado', 'não informado'
        ) then null
        else trim({{ column_name }})
    end
{% endmacro %}