import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.calcite.avatica.util.Quoting;
import org.apache.calcite.config.Lex;
import org.apache.calcite.sql.SqlKind;
import org.apache.calcite.sql.parser.SqlParseException;
import org.apache.calcite.sql.parser.SqlParser;
import org.apache.calcite.sql.parser.ddl.SqlDdlParserImpl;
import org.apache.calcite.sql.validate.SqlDelegatingConformance;

/** Syntax only: deliberately never resolve functions, validate, or build plans. */
class CalciteSyntaxCheck {
    public static void main(String[] args) throws Exception {
        ObjectMapper mapper = new ObjectMapper();
        String[] queries = mapper.readValue(System.in, String[].class);
        String[] errors = new String[queries.length];
        boolean statementListMode = args.length == 1 && args[0].equals("--statements");
        // Use QED's lexical configuration, allowing SQLite's != spelling.
        SqlParser.Config config = SqlParser.Config.DEFAULT
                .withLex(Lex.MYSQL).withQuoting(Quoting.DOUBLE_QUOTE)
                .withConformance(new SqlDelegatingConformance(SqlParser.Config.DEFAULT.conformance()) {
                    @Override public boolean isBangEqualAllowed() {
                        return true;
                    }
                });
        if (statementListMode) {
            config = config.withParserFactory(SqlDdlParserImpl.FACTORY);
        }
        for (int i = 0; i < queries.length; i++) {
            try {
                // Accept a trailing semicolon, but require exactly one query.
                var statements = SqlParser.create(queries[i], config).parseStmtList();
                if (statementListMode && statements.isEmpty()) {
                    errors[i] = "Expected at least one SQL statement";
                } else if (!statementListMode
                        && (statements.size() != 1 || !statements.get(0).isA(SqlKind.QUERY))) {
                    errors[i] = "Expected exactly one SELECT query";
                }
            } catch (SqlParseException e) {
                errors[i] = e.getMessage();
            }
        }
        mapper.writeValue(System.out, errors);
    }
}
