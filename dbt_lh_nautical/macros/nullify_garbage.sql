{% macro nullify_garbage(column_name) %}
    case 
        when trim(lower({{ column_name }})) in (
            'asdf', '???', '??', '?', 'fixme', 'n/a', 'null', 'test', 'todo', 'xxx', '.', 'tbd', '--', '-', 'TODO: add description'
        ) then null
        else trim(lower({{ column_name }}))
    end
{% endmacro %}