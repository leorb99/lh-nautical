{% macro nullify_garbage(column_name) %}
    -- Comparison is case-insensitive, but the returned value preserves the
    -- original casing (only trimmed). Do not lowercase the else branch: it
    -- previously forced every non-garbage value permanently to lowercase,
    -- which has nothing to do with detecting placeholder/garbage text.
    case
        when trim(lower({{ column_name }})) in (
            'asdf', '???', '??', '?', 'fixme', 'n/a', 'null', 'test', 'todo', 'xxx', '.', 'tbd', '--', '-', 'TODO: add description'
        ) then null
        else trim({{ column_name }})
    end
{% endmacro %}